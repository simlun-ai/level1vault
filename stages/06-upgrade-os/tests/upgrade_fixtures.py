"""Small deterministic releases and boot files for disposable upgrade tests."""

import hashlib
import json


def make_release(root, text="new OS"):
    root.mkdir()
    files = {}
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
    manifest = {
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
    return manifest


def make_boot(root, uuid="AAAA-BBBB"):
    for name, content in {
        "EFI/BOOT/BOOTX64.EFI": "preserved loader",
        "boot/grub/grub.cfg": f"search --fs-uuid --set=root {uuid}\nlinux /live/vmlinuz boot=live nopersistence live-media=/dev/disk/by-uuid/{uuid}\ninitrd /live/initrd.img\n",
        "live/filesystem.squashfs": "old OS",
        ".disk/live-uuid-old": "old marker",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
