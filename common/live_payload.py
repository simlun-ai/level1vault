#!/usr/bin/env python3
"""Extract the live OS payload shared by fresh image assembly and OS upgrades."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from contracts import Release, ReleasePayload


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def metadata(squash: Path, name: str) -> str:
    return subprocess.check_output(
        ["unsquashfs", "-cat", str(squash), "usr/local/share/level1vault/" + name],
        text=True,
    ).strip()


def extract(iso: Path, destination: Path) -> Release:
    # Select the matching live filesystem, kernel and initramfs.
    destination.mkdir()
    with tempfile.TemporaryDirectory(prefix="live-payload-") as temporary:
        source = Path(temporary) / "iso"
        subprocess.run(
            [
                "xorriso",
                "-osirrox",
                "on",
                "-indev",
                str(iso),
                "-extract",
                "/",
                str(source),
            ],
            check=True,
        )
        squash = source / "live/filesystem.squashfs"
        kernel = metadata(squash, "kernel-version")
        if not re.fullmatch(r"[0-9][a-zA-Z0-9.+_-]*", kernel):
            raise ValueError("Invalid kernel metadata")
        selected = {"filesystem.squashfs": squash}
        for name in ("vmlinuz", "initrd.img"):
            candidate = source / "live" / (name + "-" + kernel)
            selected[name] = (
                candidate if candidate.is_file() else source / "live" / name
            )

        # Export regular FAT-compatible files and the live-media UUID markers.
        live = destination / "live"
        live.mkdir()
        for name, path in selected.items():
            shutil.copyfile(path, live / name)
        (live / "packages.tsv").write_text(metadata(squash, "packages.tsv") + "\n")
        if (source / ".disk").is_dir():
            shutil.copytree(source / ".disk", destination / ".disk")

        # Make the exported payload writable by the receiving workstation user.
        # ISO directory modes can be read-only; BuildKit needs to populate them during local export.
        destination.chmod(0o755)
        for path in sorted(destination.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Payload contains a symlink: {path}")
            path.chmod(0o755 if path.is_dir() else 0o644)

        # Record the exact payload contents and their source revision.
        files: dict[str, str] = {}
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                if path.stat().st_size >= 4294967296:
                    raise ValueError(f"Payload file exceeds FAT32 limit: {path}")
                files[path.relative_to(destination).as_posix()] = digest(path)
        release: ReleasePayload = {
            "format": 1,
            "kernel": kernel,
            "zfs_package": metadata(squash, "zfsutils-version"),
            "source_commit": metadata(squash, "source-commit"),
            "files": files,
        }
        result: Release = {
            **release,
            "release_id": hashlib.sha256(
                json.dumps(release, sort_keys=True).encode()
            ).hexdigest(),
        }
        (live / "release.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n"
        )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iso", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    extract(args.iso.resolve(), args.destination.resolve())
