#!/usr/bin/env python3
"""Build or reuse an inspected OS release and upgrade one existing USB pair."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'stages/06-upgrade-os'))
import upgrade


def git(*args):
    return subprocess.check_output(['git', '-c', 'safe.directory=' + str(ROOT), *args], cwd=ROOT, text=True).strip()


def release_directory(explicit):
    # Reuse only a complete release built from the current committed source and configuration.
    if explicit:
        return Path(explicit).resolve()
    upgrade.require(not git('status', '--porcelain', '--untracked-files=no'), 'Commit tracked changes before building an OS upgrade')
    commit = git('rev-parse', 'HEAD')
    config = {'debian_image': os.environ.get('DEBIAN_IMAGE', 'debian:trixie'), 'payload_format': 1}
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]
    suffix = '-' + uuid.uuid4().hex[:8] if os.environ.get('FRESH', '0') == '1' else ''
    return (ROOT / 'build/upgrades' / (commit + '-' + fingerprint + suffix)).resolve()


def execute(pair, disk_a, disk_b, explicit=None):
    # Authenticate once and inspect both sticks before the long-running build.
    upgrade.prerequisites()
    with open('/run/lock/level1vault-write-usb.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        expected = upgrade.inspect_pair(pair, disk_a, disk_b)
        release = release_directory(explicit)
        if not release.exists():
            upgrade.require(not explicit, 'UPGRADE_DIR must point to an existing complete release')
            env = dict(os.environ, PAIR=pair)
            subprocess.run(['bash', str(ROOT / 'pipeline/buildkit.sh'), 'build-upgrade', str(release)],
                           check=True, env=env, stdin=subprocess.DEVNULL)
            upgrade.load_release(release)
        upgrade.require((release / 'UPGRADE-READY').is_file(),
                        'Incomplete upgrade release; preserve diagnostics and use FRESH=1 to build a new release')
        print(f'OS release: {release}', flush=True)
        return upgrade.apply_release(release, pair, disk_a, disk_b, expected=expected)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair', required=True)
    parser.add_argument('--disk-a', required=True, type=Path)
    parser.add_argument('--disk-b', required=True, type=Path)
    parser.add_argument('--release')
    args = parser.parse_args()
    try:
        upgrade.require(re.fullmatch(r'[1-9][0-9]*', args.pair), 'PAIR must be a positive integer')
        upgrade.require(os.environ.get('FRESH', '0') in ('0', '1'), 'FRESH must be 0 or 1')
        execute(args.pair, args.disk_a, args.disk_b, args.release)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'OS upgrade stopped: {error}. Original ZFS partitions must remain exported; inspect the report and rerun the same OS release after resolving the error.')
    except KeyboardInterrupt:
        sys.exit('OS upgrade interrupted. Inspect any retained mounts and rerun the same release to complete the OS copy.')
