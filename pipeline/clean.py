#!/usr/bin/env python3
"""Refuse BuildKit cleanup while checkout or selected-build resources are in use."""
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def inside(path, parent):
    return path == parent or parent in path.parents


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def mountinfo():
    return Path('/proc/self/mountinfo').read_text()


def check_idle(build, mountinfo, containers, loops):
    """Fail closed before deletion; caller provides observed host state."""
    for line in mountinfo.splitlines():
        target = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4]))
        if inside(target, build):
            raise ValueError(f'Mounted filesystem under build directory: {target}')

    # Reject running containers that use the build directory.
    for container in containers:
        for mount in container.get('Mounts', []):
            source = Path(mount.get('Source', '/')).resolve()
            if inside(source, build) or inside(build, source):
                raise ValueError(f'Running container uses build directory: {container.get("Id", "unknown")}')

    # Reject attached images from the build directory.
    for device in loops.get('loopdevices', []):
        backing = Path(device['back-file'].removesuffix(' (deleted)')).resolve()
        if inside(backing, build):
            raise ValueError(f'Loop device still attached: {device["name"]}')


def check_checkout_idle(build):
    ids = output('docker', 'ps', '-q').split()
    containers = json.loads(output('docker', 'inspect', *ids)) if ids else []
    loops = json.loads(output('losetup', '--json', '--list', '--output', 'NAME,BACK-FILE'))
    mounts = mountinfo()
    check_idle(ROOT, mounts, containers, loops)
    check_idle(build.resolve(), mounts, containers, loops)


if __name__ == '__main__':
    try:
        check_checkout_idle(Path(sys.argv[1]))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'Cleanup refused: {error}')
