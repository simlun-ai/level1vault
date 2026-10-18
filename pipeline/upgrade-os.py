#!/usr/bin/env python3
"""Apply an explicitly prepared OS release to one existing USB pair."""
import argparse
import fcntl
from pathlib import Path
import re
import subprocess
import sys

# Host helpers must not leave root-owned bytecode in the checkout.
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'stages/06-upgrade-os'))
import upgrade


def execute(pair, disk_a, disk_b, explicit):
    # Validate the prepared release before inspecting or writing USB disks.
    upgrade.require(bool(explicit), 'Set UPGRADE_DIR to a release from make prepare-upgrade')
    release = Path(explicit).resolve()
    upgrade.require((release / 'UPGRADE-READY').is_file(),
                    'UPGRADE_DIR must contain a complete release from make prepare-upgrade')
    upgrade.load_release(release)
    upgrade.prerequisites()
    with open('/run/lock/level1vault-write-usb.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        expected = upgrade.inspect_pair(pair, disk_a, disk_b)
        print(f'OS release: {release}', flush=True)
        return upgrade.apply_release(release, pair, disk_a, disk_b, expected=expected)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair', required=True)
    parser.add_argument('--disk-a', required=True, type=Path)
    parser.add_argument('--disk-b', required=True, type=Path)
    parser.add_argument('--release', required=True)
    args = parser.parse_args()
    try:
        upgrade.require(re.fullmatch(r'[1-9][0-9]*', args.pair), 'PAIR must be a positive integer')
        execute(args.pair, args.disk_a, args.disk_b, args.release)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'OS upgrade stopped: {error}. Original ZFS partitions must remain exported; inspect the report and rerun the same OS release after resolving the error.')
    except KeyboardInterrupt:
        sys.exit('OS upgrade interrupted. Inspect any retained mounts and rerun the same release to complete the OS copy.')
