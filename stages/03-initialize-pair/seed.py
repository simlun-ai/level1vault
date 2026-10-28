#!/usr/bin/env python3
"""Populate freshly initialized image replicas from pair 1 before USB writing."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Host helpers must not leave root-owned bytecode in the checkout.
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "common"))
from artifact_owner import publish  # noqa: E402 - resolve shared helpers after configuring their path


def output(*args):
    return subprocess.check_output(list(map(str, args)), text=True).strip()


def command(*args):
    subprocess.run(list(map(str, args)), check=True)


def imported_pools():
    return dict(
        line.split("\t")
        for line in output("zpool", "list", "-H", "-o", "name,guid").splitlines()
    )


def release(resources):
    # Export only this run's pools, retaining loops whenever ownership is uncertain.
    pools = imported_pools()
    errors = []
    for pool, guid, loops in reversed(resources):
        try:
            if pool in pools:
                if pools[pool] != guid:
                    raise RuntimeError(f"Unexpected pool GUID for {pool}")
                command("zpool", "export", pool)
            for loop in loops:
                command("losetup", "-d", loop)
        except (RuntimeError, subprocess.CalledProcessError) as error:
            errors.append(f"{pool}: {error}; inspect retained loops {loops}")
    if errors:
        raise RuntimeError("; ".join(errors))


def seed(build, count):
    # Check all pair identities and reserve their final readiness markers.
    pairs = [build / "pairs" / str(pair) for pair in range(1, count + 1)]
    records = []
    occupied = imported_pools()
    for pair, out in enumerate(pairs, 1):
        if not (out / "READY").is_file() or (out / "INITIALIZING").exists():
            raise RuntimeError(f"Use a completed initialized pair: {out}")
        info = dict(
            line.split("=", 1)
            for line in (out / "WORKSPACE-INFO.txt").read_text().splitlines()
            if "=" in line
        )
        pool = f"level1-workspace-{pair}"
        if info.get("pair") != str(pair) or info.get("state") != (
            "active" if pair == 1 else "replica"
        ):
            raise RuntimeError(f"Unexpected identity/state in {out}")
        if pool in occupied:
            raise RuntimeError(f"{pool} already imported; refusing to use it")
        records.append((pool, info["pool_guid"]))
    for out in pairs:
        (out / "READY").unlink()

    # Import only the new image pools through a private device search directory.
    resources = []
    with tempfile.TemporaryDirectory(prefix="seed-", dir=build) as temporary:
        search = Path(temporary)
        try:
            for out, (pool, guid) in zip(pairs, records, strict=False):
                loops = []
                resources.append((pool, guid, loops))
                devices = search / pool
                devices.mkdir()
                manifest = json.loads((out / "images.json").read_text())
                for side in ("a", "b"):
                    image = out / f"usb-{side}.img"
                    if (
                        image.is_symlink()
                        or not image.is_file()
                        or output("losetup", "-j", image)
                    ):
                        raise RuntimeError(
                            f"Image must be a detached regular file: {image}"
                        )
                    loop = output("losetup", "--find", "--show", "--partscan", image)
                    loops.append(loop)
                    command("udevadm", "settle", "--timeout=30")
                    partition = loop + "p2"
                    actual = output("blkid", "-s", "PARTUUID", "-o", "value", partition)
                    if actual != manifest["images"][side]["zfs_partuuid"]:
                        raise RuntimeError(f"Partition identity mismatch: {partition}")
                    (devices / side).symlink_to(partition)
                command(
                    "zpool",
                    "import",
                    "-N",
                    "-d",
                    devices,
                    "-R",
                    search,
                    "-o",
                    "cachefile=none",
                    guid,
                )

            # Replicate one shared encrypted checkpoint without loading the passphrase.
            if count > 1:
                command(
                    ROOT / "runtime/workspace-sync", "1", *map(str, range(2, count + 1))
                )
        finally:
            release(resources)

    # Publish readiness only after all pools are exported and loops detached.
    for out in pairs:
        (out / "READY").touch()
        publish(out / "READY")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path)
    parser.add_argument("count", type=int)
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise ValueError("Run image seeding as root on the host")
        if args.count < 1:
            raise ValueError("Pair count must be positive")
        seed(args.build.resolve(), args.count)
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(
            f"Image seeding failed: {error}. Inspect pools/loops before recovery; do not flash incomplete pairs."
        )
