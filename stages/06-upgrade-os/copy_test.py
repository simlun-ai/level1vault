#!/usr/bin/env python3
"""Apply an OS release only to a disposable regular image file for QEMU testing."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from upgrade import digest, load_release, require


def apply_to_copy(image: Path, release: Path):
    # Restrict test updates to regular files and validate the release first.
    require(
        image.is_file() and not image.is_symlink(),
        "Expected a disposable regular image file",
    )
    load_release(release)
    device = str(image) + "@@1048576"
    with tempfile.TemporaryDirectory(prefix="upgrade-readback-") as temporary:
        root = Path(temporary)

        # Preserve both disk-specific boot files across the test update.
        originals: dict[str, str] = {}
        for name in ("EFI/BOOT/BOOTX64.EFI", "boot/grub/grub.cfg"):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["mcopy", "-i", device, "::/" + name, str(target)], check=True
            )
            originals[name] = digest(target)

        # Replace the same payload directories as the physical updater.
        for name in ("live", ".disk"):
            exists = (
                subprocess.run(
                    ["mdir", "-i", device, "::/" + name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                == 0
            )
            if exists:
                subprocess.run(["mdeltree", "-i", device, "::/" + name], check=True)
            if (release / name).exists():
                subprocess.run(
                    ["mcopy", "-s", "-i", device, str(release / name), "::/"],
                    check=True,
                )

        # Check boot-file preservation on the disposable filesystem.
        for index, (name, checksum) in enumerate(originals.items()):
            target = root / ("readback-" + str(index))
            subprocess.run(
                ["mcopy", "-i", device, "::/" + name, str(target)], check=True
            )
            require(digest(target) == checksum, f"Test-copy readback mismatch: {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("release", type=Path)
    args = parser.parse_args()
    apply_to_copy(args.image, args.release.resolve())
