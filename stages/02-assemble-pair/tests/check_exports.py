#!/usr/bin/env python3
"""Verify that separately exported pairs have independent disk identities."""

import json
import os
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
seen = set()
for pair in (1, 2):
    out = root / f"pair-{pair}"
    assert (out / "IMAGES-PREPARED").exists() and not (out / "READY").exists()
    manifest = json.loads((out / "images.json").read_text())
    assert not (out / "pair.tar").exists()
    for disk in manifest["images"].values():
        image = (out / disk["file"]).stat()
        assert image.st_size == manifest["image_mib"] * 1024 * 1024, (
            "Wrong logical image size"
        )
        assert image.st_blocks * 512 < image.st_size // 2, (
            "Sparse holes lost during export"
        )
        assert image.st_uid == os.getuid(), "Image is not owned by the invoking user"
        for prop in ("esp_uuid", "zfs_partuuid"):
            assert disk[prop] not in seen, "BuildKit reused a disk identity"
            seen.add(disk[prop])
