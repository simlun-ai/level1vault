#!/usr/bin/env python3
"""Extract the live OS payload shared by fresh image assembly and OS upgrades."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def metadata(squash, name):
    return subprocess.check_output(['unsquashfs', '-cat', str(squash),
                                    'usr/local/share/level1vault/' + name], text=True).strip()


def extract(iso, destination):
    # Select the matching live filesystem, kernel and initramfs.
    destination.mkdir()
    with tempfile.TemporaryDirectory(prefix='live-payload-') as temporary:
        source = Path(temporary) / 'iso'
        subprocess.run(['xorriso', '-osirrox', 'on', '-indev', str(iso), '-extract', '/', str(source)], check=True)
        squash = source / 'live/filesystem.squashfs'
        kernel = metadata(squash, 'kernel-version')
        if not re.fullmatch(r'[0-9][a-zA-Z0-9.+_-]*', kernel):
            raise ValueError('Invalid kernel metadata')
        selected = {'filesystem.squashfs': squash}
        for name in ('vmlinuz', 'initrd.img'):
            candidate = source / 'live' / (name + '-' + kernel)
            selected[name] = candidate if candidate.is_file() else source / 'live' / name

        # Export regular FAT-compatible files and the live-media UUID markers.
        live = destination / 'live'
        live.mkdir()
        for name, path in selected.items():
            shutil.copyfile(path, live / name)
        (live / 'packages.tsv').write_text(metadata(squash, 'packages.tsv') + '\n')
        if (source / '.disk').is_dir():
            shutil.copytree(source / '.disk', destination / '.disk')
        files = {}
        for path in sorted(destination.rglob('*')):
            if path.is_symlink():
                raise ValueError(f'Payload contains a symlink: {path}')
            if path.is_file():
                if path.stat().st_size >= 4294967296:
                    raise ValueError(f'Payload file exceeds FAT32 limit: {path}')
                files[path.relative_to(destination).as_posix()] = digest(path)
        release = {'format': 1, 'kernel': kernel, 'zfs_package': metadata(squash, 'zfsutils-version'),
                   'source_commit': metadata(squash, 'source-commit'), 'files': files}
        release['release_id'] = hashlib.sha256(json.dumps(release, sort_keys=True).encode()).hexdigest()
        (live / 'release.json').write_text(json.dumps(release, indent=2, sort_keys=True) + '\n')
    return release


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('iso', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    extract(args.iso.resolve(), args.destination.resolve())
