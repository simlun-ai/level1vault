#!/usr/bin/env python3
"""Report host build, USB and test prerequisites without changing the system."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

# Keep package advice beside the commands each workstation workflow needs.
GROUPS = {
    'Build and container tests': {
        'bash': 'bash', 'sh': 'dash', 'git': 'git', 'make': 'make', 'python3': 'python3',
        'sudo': 'sudo', 'docker': 'docker.io', 'awk': 'mawk', 'grep': 'grep',
        'sed': 'sed', 'find': 'findutils',
        **dict.fromkeys(('realpath', 'mktemp', 'chown', 'chmod', 'cp', 'mkdir', 'rm', 'cat',
                         'tee', 'head', 'install', 'dirname', 'uname', 'env', 'id', 'ls', 'sort', 'date', 'truncate', 'touch', 'rmdir'), 'coreutils'),
    },
    'Initialization, USB writing and upgrades': {
        **dict.fromkeys(('zpool', 'zfs', 'zdb', 'zgenhostid'), 'zfsutils-linux'),
        **dict.fromkeys(('losetup', 'blkid', 'wipefs', 'flock', 'lsblk', 'blockdev',
                         'mount', 'umount', 'mountpoint', 'findmnt', 'setpriv'), 'util-linux'),
        'modprobe': 'kmod', 'udevadm': 'udev', 'sgdisk': 'gdisk', 'sfdisk': 'fdisk',
        'partprobe': 'parted', 'fsck.vfat': 'dosfstools', 'sync': 'coreutils',
    },
    'Local lint and unit tests': {'shellcheck': 'shellcheck', 'dpkg-deb': 'dpkg'},
    'QEMU and upgrade acceptance tests': {
        'qemu-system-x86_64': 'qemu-system-x86', 'qemu-img': 'qemu-utils',
        **dict.fromkeys(('mcopy', 'mdir', 'mdeltree'), 'mtools'),
    },
    'Disposable-VM ZFS integration tests': {'mkfs.vfat': 'dosfstools', 'setpriv': 'util-linux'},
}


def probe(*args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
        return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)


def check(code=None, variables=None):
    # Collect every missing executable, module and firmware dependency in one pass.
    packages = set()
    failed = False
    for group, tools in GROUPS.items():
        missing = [tool for tool in tools if shutil.which(tool) is None]
        print(f'{group}: ' + ('missing ' + ', '.join(missing) if missing else 'OK'))
        packages.update(tools[tool] for tool in missing)
    if importlib.util.find_spec('pexpect') is None:
        print('QEMU Python dependency: missing pexpect')
        packages.add('python3-pexpect')
    paths = [(Path(code), Path(variables))] if code and variables else [
        tuple(Path(f'/usr/share/OVMF/OVMF_{kind}{suffix}.fd') for kind in ('CODE', 'VARS'))
        for suffix in ('_4M', '')]
    if not any(all(path.is_file() for path in pair) for pair in paths):
        print('QEMU firmware: missing matching non-Secure-Boot OVMF CODE/VARS files (ovmf)')
        packages.add('ovmf')

    # Check readiness separately; package installation alone cannot fix permissions or kernels.
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        print('Host readiness: a Linux amd64 workstation is required')
        failed = True
    if shutil.which('docker'):
        ok, detail = probe('docker', 'buildx', 'version')
        if not ok:
            # Docker CE uses a different plugin package from distribution Docker.
            ce, status = probe('dpkg-query', '-W', '-f=${Status}', 'docker-ce')
            packages.add('docker-buildx-plugin' if ce and status == 'install ok installed' else 'docker-buildx')
            print('Docker Buildx: missing or unusable: ' + detail)
        ok, detail = probe('docker', 'info', '--format', '{{json .}}')
        if ok:
            try:
                info = json.loads(detail)
                ok = (info.get('OSType') == 'linux' and info.get('Architecture') in ('x86_64', 'amd64')
                      and not any('rootless' in option for option in info.get('SecurityOptions', [])))
            except (ValueError, AttributeError):
                ok = False
        endpoint_ok, endpoint = probe('docker', 'context', 'inspect', '--format', '{{(index .Endpoints "docker").Host}}')
        endpoint = os.environ.get('DOCKER_HOST', endpoint) if not os.environ.get('DOCKER_CONTEXT') else endpoint
        if not ok or not endpoint_ok or not endpoint.startswith('unix://'):
            print('Docker readiness: require accessible local rootful amd64 Linux Docker; check docker info and your Docker context')
            failed = True
    else:
        packages.add('docker-buildx')
    if not Path('/sys/module/zfs').is_dir():
        print('ZFS readiness: module not loaded; run sudo modprobe zfs, then rerun this check')
        failed = True
    elif shutil.which('zpool'):
        ok, detail = probe('zpool', 'list', '-H')
        if not ok:
            print('ZFS readiness: cannot query pools; check sudo zpool list and your host ZFS/kernel installation: ' + detail)
            failed = True
    if packages:
        print('Install missing dependencies with:\n  sudo apt install -y ' + ' '.join(sorted(packages)))
        print('Docker package names assume your distribution repositories; Docker CE uses docker-ce and docker-buildx-plugin.')
    if not packages and not failed:
        print('All host prerequisites are available. Real ZFS integration still runs only in an explicitly enabled disposable VM.')
    return 1 if packages or failed else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ovmf-code')
    parser.add_argument('--ovmf-vars')
    args = parser.parse_args()
    if bool(args.ovmf_code) != bool(args.ovmf_vars):
        parser.error('Specify both --ovmf-code and --ovmf-vars')
    raise SystemExit(check(args.ovmf_code, args.ovmf_vars))
