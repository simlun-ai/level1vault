"""Small deterministic releases and boot files for disposable upgrade tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from contracts import Release


def make_release(root: Path, text: str = "new OS") -> Release:
    root.mkdir()
    files: dict[str, str] = {}
    for name in (
        "live/vmlinuz",
        "live/initrd.img",
        "live/filesystem.squashfs",
        "live/packages.tsv",
        ".disk/live-uuid-new",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + ":" + name)
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest: dict[str, object] = {
        "format": 1,
        "kernel": "6.12-fixture",
        "zfs_package": "2.3-fixture",
        "source_commit": "1" * 40,
        "files": files,
    }
    manifest["release_id"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()
    ).hexdigest()
    (root / "live/release.json").write_text(json.dumps(manifest))
    (root / "UPGRADE-READY").touch()
    return cast("Release", manifest)


def make_boot(root: Path, uuid: str = "AAAA-BBBB"):
    for name, content in {
        "EFI/BOOT/BOOTX64.EFI": "preserved loader",
        "boot/grub/grub.cfg": f"search --fs-uuid --set=root {uuid}\nlinux /live/vmlinuz boot=live nopersistence live-media=/dev/disk/by-uuid/{uuid}\ninitrd /live/initrd.img\n",
        "live/filesystem.squashfs": "old OS",
        ".disk/live-uuid-old": "old marker",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
