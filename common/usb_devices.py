"""Read and validate USB disk identities shared by provisioning and OS upgrades."""

import json
import stat
from collections.abc import Iterator
from pathlib import Path

from contracts import Disk, Query


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def describe_disk(path: Path, query: Query) -> Disk:
    path = path.resolve(strict=True)
    require(stat.S_ISBLK(path.stat().st_mode), f"Not a block device: {path}")
    devices = json.loads(
        query(
            "lsblk",
            "--json",
            "--bytes",
            "--paths",
            "--output",
            "NAME,TYPE,SIZE,TRAN,RO,MOUNTPOINTS,MODEL,SERIAL",
            str(path),
        )
    )["blockdevices"]
    require(len(devices) == 1, f"Expected one whole disk: {path}")
    info = devices[0]
    info["device_id"] = path.stat().st_rdev
    require(
        Path(info["name"]).resolve() == path, "Disk identity changed during inspection"
    )
    return info


def descendants(info: Disk) -> Iterator[Disk]:
    yield info
    for child in info.get("children") or ():
        yield from descendants(child)


def validate_idle(info: Disk, imported: set[Path]) -> None:
    # Reject partitions, non-USB disks and devices still in use.
    require(
        info["type"] == "disk" and info.get("tran", None) == "usb",
        f"Select a whole USB disk: {info['name']}",
    )
    for device in descendants(info):
        name = device["name"]
        require(
            device["type"] in ("disk", "part"), f"Device has active holders: {name}"
        )
        require(not device.get("ro", False), f"Read-only device: {name}")
        require(
            not any(device.get("mountpoints", None) or []),
            f"Mounted filesystem or swap on {name}; unmount it first",
        )
        require(
            Path(name).resolve() not in imported,
            f"Imported ZFS pool uses {name}; export it first",
        )


def imported_devices(query: Query) -> set[Path]:
    # Identify imported members without importing or changing any pool.
    devices: set[Path] = set()
    for pool in query("zpool", "list", "-H", "-o", "name").splitlines():
        for line in query("zpool", "status", "-LP", pool).splitlines():
            fields = line.split()
            if fields and fields[0].startswith("/"):
                devices.add(Path(fields[0]).resolve())
    return devices
