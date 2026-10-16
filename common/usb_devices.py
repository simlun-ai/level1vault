"""Read and validate USB disk identities shared by provisioning and OS upgrades."""
import json
from pathlib import Path
import stat


def require(condition, message):
    if not condition:
        raise ValueError(message)


def describe_disk(path, query):
    path = path.resolve(strict=True)
    require(stat.S_ISBLK(path.stat().st_mode), f'Not a block device: {path}')
    devices = json.loads(query('lsblk', '--json', '--bytes', '--paths', '--output',
                               'NAME,TYPE,SIZE,TRAN,RO,MOUNTPOINTS,MODEL,SERIAL', str(path)))['blockdevices']
    require(len(devices) == 1, f'Expected one whole disk: {path}')
    info = devices[0]
    info['device_id'] = path.stat().st_rdev
    require(Path(info['name']).resolve() == path, 'Disk identity changed during inspection')
    return info


def descendants(info):
    yield info
    for child in info.get('children', []):
        yield from descendants(child)


def validate_idle(info, imported):
    # Reject partitions, non-USB disks and devices still in use.
    require(info['type'] == 'disk' and info.get('tran') == 'usb', f"Select a whole USB disk: {info['name']}")
    for device in descendants(info):
        name = device['name']
        require(device['type'] in ('disk', 'part'), f'Device has active holders: {name}')
        require(not device.get('ro'), f'Read-only device: {name}')
        require(not any(device.get('mountpoints') or []), f'Mounted filesystem or swap on {name}; unmount it first')
        require(Path(name).resolve() not in imported, f'Imported ZFS pool uses {name}; export it first')


def imported_devices(query):
    # Identify imported members without importing or changing any pool.
    devices = set()
    for pool in query('zpool', 'list', '-H', '-o', 'name').splitlines():
        for line in query('zpool', 'status', '-LP', pool).splitlines():
            fields = line.split()
            if fields and fields[0].startswith('/'):
                devices.add(Path(fields[0]).resolve())
    return devices
