#!/usr/bin/env python3
"""Replace existing USB OS payloads in place while preserving workspace partitions."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'common'))
from live_payload import digest
from usb_devices import describe_disk, imported_devices, require, validate_idle

ESP_TYPE = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
ZFS_TYPE = '6a898cc3-1dd2-11b2-99a6-080020736631'
OS_FILES = {'live/vmlinuz', 'live/initrd.img', 'live/filesystem.squashfs', 'live/packages.tsv'}
TOOLS = ('lsblk', 'zpool', 'zdb', 'sfdisk', 'blkid', 'fsck.vfat', 'mount', 'umount', 'sync')


def output(*args):
    return subprocess.check_output(list(map(str, args)), text=True).strip()


def command(*args):
    subprocess.run(list(map(str, args)), check=True)


def load_release(release):
    # Verify the release identity and every permitted payload file before USB access.
    manifest = json.loads((release / 'live/release.json').read_text())
    require(manifest.get('format') == 1, 'Unsupported OS release format')
    identity = dict(manifest)
    release_id = identity.pop('release_id')
    require(hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest() == release_id,
            'OS release metadata checksum mismatch')
    require(re.fullmatch(r'[0-9a-f]{40}', manifest['source_commit']) is not None, 'Invalid source revision')
    files = manifest['files']
    require(OS_FILES.issubset(files), 'Incomplete OS payload')
    for name, checksum in files.items():
        path = PurePosixPath(name)
        require(not path.is_absolute() and '..' not in path.parts and path.as_posix() == name,
                'Unsafe payload path')
        require(name in OS_FILES or (len(path.parts) > 1 and path.parts[0] == '.disk'), 'Unexpected payload file')
        source = release / name
        require(source.is_file() and not any(part.is_symlink() for part in [source, *source.parents]),
                f'Payload must be a regular file without symlinks: {name}')
        require(source.stat().st_size < 4294967296, f'File exceeds FAT32 limit: {name}')
        require(digest(source) == checksum, f'Payload checksum mismatch: {name}')
    return manifest


def label_identity(text):
    # Read offline ZFS membership without importing or unlocking the workspace.
    identities = set()
    for label in re.split(r'LABEL\s+[0-9]+', text):
        pool = re.search(r"^\s*name:\s*'([^']+)'", label, re.M)
        guid = re.search(r'^\s*pool_guid:\s*([0-9]+)', label, re.M)
        leaf = re.search(r'^\s*guid:\s*([0-9]+)', label, re.M)
        if pool and guid and leaf:
            require(re.search(r"type:\s*'mirror'", label) is not None, 'Expected a mirrored workspace')
            identities.add((pool[1], guid[1], leaf[1]))
    require(len(identities) == 1, 'ZFS labels are missing or disagree about device identity')
    return next(iter(identities))


def inspect_disk(path, imported):
    # Capture immutable device and partition identities for the upgrade report.
    info = describe_disk(path, output)
    validate_idle(info, imported)
    disk = Path(info['name'])
    table = json.loads(output('sfdisk', '--json', disk))['partitiontable']
    parts = table['partitions']
    require(table['label'] == 'gpt' and len(parts) == 2, f'Unexpected partition layout: {disk}')
    require(parts[0]['type'].lower() == ESP_TYPE and parts[1]['type'].lower() == ZFS_TYPE,
            f'Expected EFI then ZFS partitions: {disk}')
    require(output('blkid', '-p', '-s', 'TYPE', '-o', 'value', parts[0]['node']) == 'vfat', 'Expected a FAT EFI filesystem')
    esp_uuid = output('blkid', '-p', '-s', 'UUID', '-o', 'value', parts[0]['node'])
    pool, pool_guid, leaf_guid = label_identity(output('zdb', '-l', parts[1]['node']))
    return {'disk': str(disk), 'device_id': info['device_id'], 'model': info.get('model'),
            'serial': info.get('serial'), 'table': table, 'esp': parts[0]['node'],
            'esp_uuid': esp_uuid, 'data': parts[1]['node'], 'pool': pool,
            'pool_guid': pool_guid, 'leaf_guid': leaf_guid}


def inspect_pair(pair, disk_a, disk_b):
    imported = imported_devices(output)
    disks = [inspect_disk(path, imported) for path in (disk_a, disk_b)]
    require(disks[0]['device_id'] != disks[1]['device_id'], 'DISK_A and DISK_B must be different disks')
    require(all(disk['pool'] == f'workspace-{pair}' for disk in disks), 'Disks do not belong to the requested PAIR')
    require(disks[0]['pool_guid'] == disks[1]['pool_guid'], 'Disks belong to different ZFS pools')
    require(disks[0]['leaf_guid'] != disks[1]['leaf_guid'], 'Disks are copies of the same mirror member')
    require(disks[0]['esp_uuid'] != disks[1]['esp_uuid'], 'Duplicate EFI filesystem UUIDs')
    for index in (0, 1):
        require(disks[0]['table']['partitions'][index]['uuid'] != disks[1]['table']['partitions'][index]['uuid'],
                'Duplicate partition UUIDs')
    return disks


@contextmanager
def mounted(esp, readonly):
    # Mount only the EFI partition and retain the mountpoint if unmounting fails.
    mountpoint = Path(tempfile.mkdtemp(prefix='level1vault-upgrade-'))
    active = False
    try:
        command('mount', '-t', 'vfat', '-o', ('ro' if readonly else 'rw') + ',nodev,nosuid,noexec', esp, mountpoint)
        active = True
        yield mountpoint
    finally:
        if active:
            try:
                command('umount', mountpoint)
            except BaseException:
                print(f'Unmount failed; retained mountpoint: {mountpoint}', file=sys.stderr)
                raise
        mountpoint.rmdir()


def boot_identity(root, esp_uuid):
    # Preserve the existing loader and its disk-specific boot configuration.
    loader = root / 'EFI/BOOT/BOOTX64.EFI'
    config = root / 'boot/grub/grub.cfg'
    require(loader.is_file() and config.is_file(), 'Existing EFI loader/GRUB configuration is missing')
    text = config.read_text()
    require(f'--set=root {esp_uuid}' in text and f'live-media=/dev/disk/by-uuid/{esp_uuid}' in text,
            'Boot configuration does not select its own EFI filesystem')
    require('linux /live/vmlinuz ' in text and 'initrd /live/initrd.img' in text and 'nopersistence' in text,
            'Unsupported boot configuration; inspect manually')
    return {str(path.relative_to(root)): digest(path) for path in (loader, config)}


def check_space(root, release):
    # Allow in-place replacement without reserving a second complete OS copy.
    usage = os.statvfs(root)
    cluster = usage.f_frsize
    required = sum(((p.stat().st_size + cluster - 1) // cluster) * cluster
                   for p in release.rglob('*') if p.is_file() and p.relative_to(release).parts[0] in ('live', '.disk'))
    reclaimable = 0
    for name in ('live', '.disk'):
        target = root / name
        require(not target.is_symlink() and (not target.exists() or target.is_dir()), 'Unexpected OS payload path')
        if target.exists():
            for path in target.rglob('*'):
                require(not path.is_symlink(), 'Unexpected symlink on OS filesystem')
                if path.is_file():
                    reclaimable += ((path.stat().st_size + cluster - 1) // cluster) * cluster
    require(required + 8 * 1024 * 1024 <= usage.f_bavail * cluster + reclaimable,
            'New OS payload does not fit the existing EFI filesystem')


def verify_payload(root, manifest):
    for name, checksum in manifest['files'].items():
        require((root / name).is_file() and digest(root / name) == checksum, f'OS readback mismatch: {name}')
    require(json.loads((root / 'live/release.json').read_text()) == manifest, 'Installed release record differs')


def replace_payload(root, release, manifest):
    # Replace only the OS files, leaving boot files and all partition metadata intact.
    for name in ('live', '.disk'):
        if (root / name).exists():
            shutil.rmtree(root / name)
    for name in sorted(manifest['files']):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(release / name, target)
    for name, checksum in manifest['files'].items():
        require(digest(root / name) == checksum, f'OS copy verification failed: {name}')
    shutil.copyfile(release / 'live/release.json', root / 'live/release.json')
    command('sync', '-f', root)


def update_esp(esp, esp_uuid, release, manifest, boot):
    # Apply the release and verify it again after a clean unmount.
    with mounted(esp, readonly=False) as root:
        require(boot_identity(root, esp_uuid) == boot, 'Boot files changed before update')
        check_space(root, release)
        replace_payload(root, release, manifest)
        require(boot_identity(root, esp_uuid) == boot, 'Boot files changed during update')
    with mounted(esp, readonly=True) as root:
        verify_payload(root, manifest)
        require(boot_identity(root, esp_uuid) == boot, 'Boot files changed after update')


def apply_release(release, pair, disk_a, disk_b, expected=None):
    # Validate the release and both destinations before replacing either payload.
    manifest = load_release(release)
    disks = inspect_pair(pair, disk_a, disk_b)
    if expected is not None:
        require(disks == expected, 'Selected disks changed during the build; inspect and retry')
    boots = []
    for disk in disks:
        command('fsck.vfat', '-n', disk['esp'])
        with mounted(disk['esp'], readonly=True) as root:
            boots.append(boot_identity(root, disk['esp_uuid']))
            check_space(root, release)

    # Record partial completion so a failed update can be retried without guessing.
    reports = release / 'reports'
    reports.mkdir(exist_ok=True)
    report_path = reports / ('pair-' + pair + '-' + uuid.uuid4().hex[:12] + '.json')
    report = {'release_id': manifest['release_id'], 'source_commit': manifest['source_commit'],
              'pair': pair, 'status': 'running', 'disks': disks, 'boot_files': boots, 'completed': []}
    print(f'Upgrade report: {report_path}', flush=True)
    try:
        for side, disk, boot in zip(('a', 'b'), disks, boots):
            report_path.write_text(json.dumps(report, indent=2) + '\n')
            require(inspect_disk(Path(disk['disk']), imported_devices(output)) == disk, 'Disk identity or usage changed')
            print(f"Upgrading {side.upper()}: {disk['disk']} ({disk['model']}, {disk['serial']})", flush=True)
            update_esp(disk['esp'], disk['esp_uuid'], release, manifest, boot)
            require(inspect_disk(Path(disk['disk']), imported_devices(output)) == disk, 'Partition identities changed')
            report['completed'].append(side)
        report['status'] = 'passed'
    finally:
        if report['status'] != 'passed':
            report['status'] = 'failed'
        report_path.write_text(json.dumps(report, indent=2) + '\n')
    print('Both OS partitions upgraded and verified; workspace partitions were not opened for writing.')
    return report_path


def prerequisites():
    require(os.geteuid() == 0, 'Run through make upgrade-os with sudo on the host')
    for tool in TOOLS:
        require(shutil.which(tool) is not None, f'Missing host tool: {tool}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', required=True, type=Path)
    parser.add_argument('--pair', required=True)
    parser.add_argument('--disk-a', required=True, type=Path)
    parser.add_argument('--disk-b', required=True, type=Path)
    args = parser.parse_args()
    try:
        require(re.fullmatch(r'[1-9][0-9]*', args.pair), 'PAIR must be a positive integer')
        prerequisites()
        with open('/run/lock/level1vault-write-usb.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            apply_release(args.release.resolve(), args.pair, args.disk_a, args.disk_b)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'OS upgrade stopped: {error}. Keep both sticks connected until mounts are resolved; rerun the same release to repair incomplete OS copies.')
