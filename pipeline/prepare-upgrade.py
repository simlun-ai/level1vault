#!/usr/bin/env python3
"""Build and inspect an OS upgrade without accessing USB disks."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def prepare(build):
    # Refuse existing releases and source that cannot be reproduced from Git.
    if build.exists() or build.is_symlink() or ',' in str(build):
        raise ValueError('Use a new BUILD_DIR without commas; existing releases are never overwritten')
    dirty = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=ROOT, text=True)
    if dirty:
        raise ValueError('Commit source changes before preparing an OS upgrade')
    if os.environ.get('FRESH', '0') not in ('0', '1'):
        raise ValueError('FRESH must be 0 or 1')

    # BuildKit publishes UPGRADE-READY only after inspecting and exporting the payload.
    subprocess.run(['bash', str(ROOT / 'pipeline/buildkit.sh'), 'build-upgrade', str(build.resolve())],
                   check=True, stdin=subprocess.DEVNULL)
    if not (build / 'UPGRADE-READY').is_file():
        raise ValueError('Build did not publish UPGRADE-READY; preserve diagnostics and use a new BUILD_DIR')
    print(f'OS release prepared: {build.resolve()}')
    print(f'Next: identify USB disks with lsblk, then make upgrade-os UPGRADE_DIR={build} PAIR=N DISK_A=... DISK_B=...')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        prepare(args.build_dir)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'Upgrade preparation stopped: {error}')
