"""Shared identity and partition checks for replacement preparation and recovery."""
from contextlib import contextmanager
import json
import copy
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

from usb_devices import describe_disk, descendants, imported_devices, require, validate_idle

ESP_TYPE = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
ZFS_TYPE = '6a898cc3-1dd2-11b2-99a6-080020736631'


def output(*args):
    return subprocess.check_output(list(map(str, args)), text=True, env=dict(os.environ, LC_ALL='C')).strip()


def command(*args):
    subprocess.run(list(map(str, args)), check=True)


def validate_record(record):
    require(record['format'] == 1 and record['member'] in ('a', 'b'), 'Invalid recovery record')
    require(re.fullmatch(r'[1-9][0-9]*', record['pair']) is not None, 'Invalid pair ID')
    require(re.fullmatch(r'[1-9][0-9]*', record['pool_guid']) is not None, 'Invalid pool GUID')
    for key in ('failed_partuuid', 'survivor_partuuid', 'new_partuuid'):
        require(str(uuid.UUID(record[key])) == record[key], f'Invalid {key}')
    require(len({record[k] for k in ('failed_partuuid', 'survivor_partuuid', 'new_partuuid')}) == 3,
            'Replacement must have a fresh partition identity')
    require(re.fullmatch(r'[0-9A-F]{4}-[0-9A-F]{4}', record['esp_uuid']) is not None, 'Invalid FAT UUID')
    require(isinstance(record['minimum_bytes'], int) and record['minimum_bytes'] > 0, 'Invalid partition size')
    return record


def partition_table(path):
    table = json.loads(output('sfdisk', '--json', path))['partitiontable']
    parts = table['partitions']
    require(table['label'] == 'gpt' and len(parts) == 2, 'Expected exactly EFI and ZFS GPT partitions')
    require(parts[0]['type'].lower() == ESP_TYPE and parts[1]['type'].lower() == ZFS_TYPE,
            'Expected EFI then ZFS partitions')
    require(table.get('sectorsize', 512) == 512, 'Only 512-byte logical sectors are supported')
    return table


def empty_partition(path):
    signatures = json.loads(output('wipefs', '--json', '--no-act', path))['signatures']
    require(not signatures, f'Expected an empty replacement partition: {path}')


def connected_identities(record, recovering):
    # A PARTUUID symlink alone hides duplicate physical/image copies.
    devices = json.loads(output('lsblk', '--json', '--paths', '--output', 'NAME,PARTUUID,UUID'))['blockdevices']
    rows = [row for disk in devices for row in descendants(disk)]
    for key in ('failed_partuuid', 'survivor_partuuid', 'new_partuuid'):
        count = sum(row.get('partuuid', '').lower() == record[key] for row in rows if row.get('partuuid'))
        expected = int(recovering and key != 'failed_partuuid')
        require(count == expected, f'Unexpected connected copies of {key}; disconnect failed/duplicate/original members as required')
    count = sum(row.get('uuid') == record['esp_uuid'] for row in rows)
    require(count == int(recovering), 'Unexpected copies of the replacement FAT filesystem')


def inspect_replacement(disk, record, boot=False, empty=True):
    info = describe_disk(disk, output)
    table = partition_table(info['name'])
    esp, data = table['partitions']
    checked = copy.deepcopy(info)
    if boot:
        source = output('findmnt', '-n', '-o', 'SOURCE', '--mountpoint', '/run/live/medium')
        options = output('findmnt', '-n', '-o', 'OPTIONS', '--mountpoint', '/run/live/medium')
        require(Path(source).resolve() == Path(esp['node']).resolve() and 'ro' in options.split(','),
                'Boot the prepared replacement with its live medium mounted read-only')
        for child in checked.get('children', []):
            if child['name'] == esp['node']:
                require(set(filter(None, child.get('mountpoints', []))) == {'/run/live/medium'},
                        'Replacement EFI partition has unexpected mounts')
                child['mountpoints'] = []
    imported = imported_devices(output)
    if not empty:
        imported.discard(Path(data['node']).resolve())
    validate_idle(checked, imported)
    require(data['uuid'].lower() == record['new_partuuid'], 'Replacement ZFS PARTUUID does not match record')
    require(data['size'] * 512 >= record['minimum_bytes'], 'Replacement partition is too small')
    require(output('blkid', '-p', '-s', 'UUID', '-o', 'value', esp['node']) == record['esp_uuid'],
            'Replacement FAT UUID does not match record')
    require(output('blkid', '-p', '-s', 'TYPE', '-o', 'value', esp['node']) == 'vfat', 'Expected FAT EFI filesystem')
    if empty:
        empty_partition(data['node'])
    return info, table


@contextmanager
def mounted_readonly(esp):
    directory = Path(tempfile.mkdtemp(prefix='level1vault-member-'))
    active = False
    try:
        command('mount', '-t', 'vfat', '-o', 'ro,nodev,nosuid,noexec', esp, directory)
        active = True
        yield directory
    finally:
        if active:
            command('umount', directory)
        directory.rmdir()


def label_identity(text):
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


def mirror_leaves(status):
    # Reject extra top-level vdevs, spares and replacement trees rather than guessing.
    config = status.split('config:', 1)[1].split('errors:', 1)[0]
    rows = [match.groups() for match in re.finditer(
        r'^([ \t]+)(\S+)[ \t]+(\S+)[ \t]+(\d+)[ \t]+(\d+)[ \t]+(\d+)(?:[ \t]+was[ \t]+.*)?[ \t]*$', config, re.M)]
    require(len(rows) == 4 and len(rows[0][0]) < len(rows[1][0]) < len(rows[2][0]) == len(rows[3][0]),
            'Expected exactly one two-member mirror; inspect interrupted replacement trees manually')
    require(all(re.fullmatch(r'[0-9]+', row[1]) for row in rows[1:]), 'Expected numeric vdev GUIDs')
    return rows[2:]
