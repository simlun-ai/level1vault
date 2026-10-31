#!/usr/bin/env python3
"""Write and read back one initialized image pair to two explicitly selected whole USB disks."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts import Disk


import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Host helpers must not leave root-owned bytecode in the checkout.
sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common"))
from usb_devices import describe_disk, imported_devices, validate_idle  # noqa: E402 - resolve shared helpers after configuring their path
from usb_io import prepare_disk, write_image  # noqa: E402 - resolve shared helpers after configuring their path
from usb_io import readback as readback  # noqa: E402 - resolve shared helpers after configuring their path


def output(*args: str | Path):
    return subprocess.check_output(args, text=True).strip()


def require(condition: object, message: str):
    if not condition:
        raise ValueError(message)


def verify_images(out: Path, pair: str):
    # Require completed regular images belonging to the selected pair.
    require(
        (out / "READY").is_file() and not (out / "INITIALIZING").exists(),
        "Use an initialized pair with READY",
    )
    require(
        f"pair={pair}" in (out / "WORKSPACE-INFO.txt").read_text().splitlines(),
        "PAIR does not match the initialized images",
    )
    for name in ("usb-a.img", "usb-b.img", "images.json"):
        image = out / name
        require(
            image.is_file() and not image.is_symlink(),
            f"Expected a regular file: {image}",
        )
        require(image.stat().st_size > 0, f"Empty source file: {image}")


def disk_info(path: Path):
    return describe_disk(path, output)


def validate_disk(info: Disk, image_size: int, imported: set[Path]):
    validate_idle(info, imported)
    require(int(info["size"]) >= image_size, f"Disk is too small: {info['name']}")


def write_pair(out: Path, pair: str, disk_a: Path, disk_b: Path):
    # Check readiness and reject source image loops before inspecting destinations.
    verify_images(out, pair)
    images = [out / "usb-a.img", out / "usb-b.img"]
    loops = json.loads(output("losetup", "--json", "--list", "--output", "BACK-FILE"))
    for loop in loops.get("loopdevices", []):
        backing = loop.get("back-file")
        require(
            not backing
            or Path(backing.removesuffix(" (deleted)")).resolve() not in images,
            "Source images have host loop mappings; detach them first",
        )

    # Identify imported pool members without importing or modifying any pool.
    imported = imported_devices(output)

    # Validate both destinations before the first destructive operation.
    disks = [disk_info(disk_a), disk_info(disk_b)]
    paths = [Path(info["name"]).resolve() for info in disks]
    require(
        disks[0]["device_id"] != disks[1]["device_id"],
        "DISK_A and DISK_B must be different disks",
    )
    for info, image in zip(disks, images, strict=False):
        validate_disk(info, image.stat().st_size, imported)
        print(
            f"Selected {image} -> {info['name']} (model={info.get('model')}, serial={info.get('serial')})",
            flush=True,
        )

    # Verify each write before GPT relocation intentionally changes disk bytes.
    for image, disk in zip(images, paths, strict=False):
        prepare_disk(disk)
        write_image(image, disk)
        subprocess.run(["sgdisk", "-e", str(disk)], check=True)
        subprocess.run(["sfdisk", "--verify", str(disk)], check=True)
    subprocess.run(["sync"], check=True)
    subprocess.run(["partprobe", *map(str, paths)], check=True)
    print(
        "Both sticks written and readback verified. Label them with pair ID and A/B, then test each boots independently."
    )


def main():
    # Require explicit destinations and serialize physical disk writes.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--pair", required=True)
    parser.add_argument("--disk-a", required=True, type=Path)
    parser.add_argument("--disk-b", required=True, type=Path)
    args = parser.parse_args()
    require(re.fullmatch(r"[1-9][0-9]*", args.pair), "PAIR must be a positive integer")
    require(os.geteuid() == 0, "Run through make write-usb with sudo on the host")
    for tool in (
        "lsblk",
        "losetup",
        "zpool",
        "blockdev",
        "wipefs",
        "sgdisk",
        "sfdisk",
        "sync",
        "partprobe",
    ):
        require(shutil.which(tool) is not None, f"Missing host tool: {tool}")
    with open("/run/lock/level1vault-write-usb.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        write_pair(args.out.resolve(), args.pair, args.disk_a, args.disk_b)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(
            f"USB writing stopped: {error}. If disk cleanup or writing started, inspect both disks before retrying."
        )
