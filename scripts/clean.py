#!/usr/bin/env python3
"""Remove only expendable live-build directories, never image/data artifacts."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

TRANSIENT = ('chroot', 'cache', 'config', 'binary')
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
    for container in containers:
        for mount in container.get('Mounts', []):
            source = Path(mount.get('Source', '/')).resolve()
            if inside(source, build) or inside(build, source):
                raise ValueError(f'Running container uses build directory: {container.get("Id", "unknown")}')
    for device in loops.get('loopdevices', []):
        backing = Path(device['back-file'].removesuffix(' (deleted)')).resolve()
        if inside(backing, build):
            raise ValueError(f'Loop device still attached: {device["name"]}')


def clean(build):
    # Refuse root, the checkout, its ancestors, and symlink aliases.
    if build.is_symlink() or build.absolute() != build.resolve():
        raise ValueError('Use the real build directory path, without symlinks or ..')
    build = build.resolve()
    if inside(ROOT, build):
        raise ValueError('Refusing repository directory or its ancestors')
    if not build.exists():
        print(f'Nothing to clean: {build}')
        return
    if not (build/'builder-image.json').is_file():
        raise ValueError('Missing builder-image.json; not a recognized build directory')
    targets = [build/name for name in TRANSIENT if (build/name).exists() or (build/name).is_symlink()]
    if not targets:
        print('No transient build directories remain. Images and records retained.')
        return
    if any(path.is_symlink() or not path.is_dir() for path in targets):
        raise ValueError('Unexpected file/symlink in transient directories; inspect manually')
    # Docker access is required: mounts in a running builder namespace may be
    # invisible in this process's mountinfo. A failed query must stop cleanup.
    ids = output('docker', 'ps', '-q').split()
    containers = json.loads(output('docker', 'inspect', *ids)) if ids else []
    loops = json.loads(output('losetup', '--json', '--list', '--output', 'NAME,BACK-FILE'))
    check_idle(build, mountinfo(), containers, loops)
    for path in targets:
        print(f'Removing {path}')
        shutil.rmtree(path)
    print('Retained ISO, image pairs, manifests and logs. Use a fresh BUILD_DIR for the next build.')


if __name__ == '__main__':
    try:
        clean(Path(sys.argv[1]))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'Cleanup refused: {error}')
