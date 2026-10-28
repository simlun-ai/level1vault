#!/usr/bin/env python3
"""Write one blank USB as an empty replacement member and verify its readback."""

import argparse
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "common"))
from artifact_owner import publish  # noqa: E402 - resolve shared helpers after configuring their path
from member_recovery import (  # noqa: E402 - resolve shared helpers after configuring their path
    command,
    connected_identities,
    inspect_replacement,
    mounted_readonly,
    output,
    require,
    validate_record,
)
from usb_devices import descendants, describe_disk, imported_devices, validate_idle  # noqa: E402 - resolve shared helpers after configuring their path
from usb_io import write_image  # noqa: E402 - resolve shared helpers after configuring their path


def write(out, pair, member, disk):
    # Require a complete, uninitialized replacement and an unattached source image.
    require(
        (out / "REPLACEMENT-READY").is_file()
        and not (out / "READY").exists()
        and not (out / "INITIALIZING").exists(),
        "Use make prepare-replacement output",
    )
    record = validate_record(json.loads((out / "member-recovery.json").read_text()))
    require(
        (record["pair"], record["member"]) == (pair, member),
        "PAIR/MEMBER do not match preparation",
    )
    image = out / f"usb-{member}.img"
    require(
        image.is_file() and not image.is_symlink(),
        "Expected a regular replacement image",
    )
    loops = json.loads(output("losetup", "--json", "--list", "--output", "BACK-FILE"))
    for loop in loops.get("loopdevices", []):
        require(
            Path((loop.get("back-file") or "").removesuffix(" (deleted)")).resolve()
            not in {out / "usb-a.img", out / "usb-b.img"},
            "Detach source image loops first",
        )
    for key in ("failed_partuuid", "survivor_partuuid"):
        require(
            not Path("/dev/disk/by-partuuid", record[key]).exists(),
            "Disconnect both original members before writing",
        )
    connected_identities(record, recovering=False)

    # Refuse an occupied, non-USB, undersized or nonblank destination before writing.
    info = describe_disk(disk, output)
    validate_idle(info, imported_devices(output))
    require(int(info["size"]) >= image.stat().st_size, "USB is too small")
    require(
        output("blockdev", "--getss", info["name"]) == "512",
        "Only 512-byte logical sectors are supported",
    )
    for device in descendants(info):
        signatures = json.loads(output("wipefs", "--json", "--no-act", device["name"]))[
            "signatures"
        ]
        require(
            all(s["type"] in ("gpt", "PMBR") for s in signatures),
            f"USB is not blank: {device['name']}; existing filesystem signatures are never erased automatically",
        )
    print(
        f"Replacement {pair}{member.upper()} -> {info['name']} (model={info.get('model')}, serial={info.get('serial')})",
        flush=True,
    )
    require(
        describe_disk(disk, output) == info, "Destination identity or usage changed"
    )
    validate_idle(info, imported_devices(output))

    # Verify the exact written bytes before relocating the backup GPT.
    target = Path(info["name"])
    write_image(image, target)
    command("sgdisk", "-e", target)
    command("sfdisk", "--verify", target)
    command("partprobe", target)
    command("udevadm", "settle")
    final, table = inspect_replacement(target, record)
    require(
        final["device_id"] == info["device_id"]
        and final.get("serial") == info.get("serial"),
        "USB identity changed",
    )
    with mounted_readonly(table["partitions"][0]["node"]) as root:
        require(
            json.loads((root / "member-recovery.json").read_text()) == record,
            "USB recovery record differs",
        )
    report = out / "write-report.json"
    report.write_text(
        json.dumps(
            dict(record=record, disk=final, status="readback-verified"), indent=2
        )
        + "\n"
    )
    publish(report)
    print(
        f"Written and verified. Label this USB {pair}{member.upper()}. Boot it, connect the survivor, then run: sudo workspace-replace {pair} --fenced"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--pair", required=True)
    parser.add_argument("--member", required=True, choices=("a", "b"))
    parser.add_argument("--disk", required=True, type=Path)
    args = parser.parse_args()
    require(os.geteuid() == 0, "Run through make write-replacement with sudo")
    with open("/run/lock/level1vault-write-usb.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        write(args.out.resolve(), args.pair, args.member, args.disk)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(
            f"Replacement write stopped: {error}. If writing started, inspect the destination before retrying."
        )
