#!/usr/bin/env python3
"""Verify that separately exported pairs have independent disk identities."""
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
seen = set()
for pair in (1, 2):
    out = root/f'pair-{pair}'
    assert (out/'IMAGES-PREPARED').exists() and not (out/'READY').exists()
    manifest = json.loads((out/'images.json').read_text())
    for disk in manifest['images'].values():
        for prop in ('esp_uuid', 'zfs_partuuid'):
            assert disk[prop] not in seen, 'BuildKit reused a disk identity'
            seen.add(disk[prop])
