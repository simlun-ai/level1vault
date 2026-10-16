#!/usr/bin/env python3
"""Write one verified image pair to two explicitly selected whole USB disks."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'common'))
from usb_devices import describe_disk, imported_devices, validate_idle


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_images(out, pair):
    # Require a completed pair and verify only its expected checksum entries.
    require((out / 'READY').is_file() and not (out / 'INITIALIZING').exists(), 'Use an initialized pair with READY')
    require(f'pair={pair}' in (out / 'WORKSPACE-INFO.txt').read_text().splitlines(), 'PAIR does not match the initialized images')
    names = {'usb-a.img', 'usb-b.img', 'images.json'}
    recorded = {}
    for line in (out / 'SHA256SUMS').read_text().splitlines():
        match = re.fullmatch(r'([a-fA-F0-9]{64}) [ *](.+)', line)
        require(match is not None, 'Malformed SHA256SUMS')
        checksum, name = match.groups()
        require(name in names and name not in recorded, 'Unexpected or duplicate checksum entry')
        recorded[name] = checksum.lower()
    require(set(recorded) == names, 'Checksums must cover both images and images.json')
    for name, checksum in recorded.items():
        image = out / name
        require(image.is_file() and not image.is_symlink(), f'Expected a regular file: {image}')
        digest = hashlib.sha256()
        with image.open('rb') as stream:
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                digest.update(block)
        require(digest.hexdigest() == checksum, f'Checksum mismatch: {image}')


def disk_info(path):
    return describe_disk(path, output)


def validate_disk(info, image_size, imported):
    validate_idle(info, imported)
    require(int(info['size']) >= image_size, f"Disk is too small: {info['name']}")


def write_pair(out, pair, disk_a, disk_b):
    # Verify source data and reject source image loops before inspecting destinations.
    verify_images(out, pair)
    images = [out / 'usb-a.img', out / 'usb-b.img']
    loops = json.loads(output('losetup', '--json', '--list', '--output', 'BACK-FILE'))
    for loop in loops.get('loopdevices', []):
        backing = loop.get('back-file')
        require(not backing or Path(backing.removesuffix(' (deleted)')).resolve() not in images,
                'Source images have host loop mappings; detach them first')

    # Identify imported pool members without importing or modifying any pool.
    imported = imported_devices(output)

    # Validate both destinations before the first destructive operation.
    disks = [disk_info(disk_a), disk_info(disk_b)]
    paths = [Path(info['name']).resolve() for info in disks]
    require(disks[0]['device_id'] != disks[1]['device_id'], 'DISK_A and DISK_B must be different disks')
    for info, image in zip(disks, images):
        validate_disk(info, image.stat().st_size, imported)
        print(f"Selected {image} -> {info['name']} (model={info.get('model')}, serial={info.get('serial')})", flush=True)

    # Write each image and relocate its backup GPT to the end of the physical disk.
    for image, disk in zip(images, paths):
        subprocess.run(['dd', f'if={image}', f'of={disk}', 'bs=4M', 'conv=fsync', 'status=progress'], check=True)
        subprocess.run(['sgdisk', '-e', str(disk)], check=True)
    subprocess.run(['sync'], check=True)
    subprocess.run(['partprobe', *map(str, paths)], check=True)
    print('Both sticks written. Label them with pair ID and A/B, then test each boots independently.')


def main():
    # Require explicit destinations and serialize physical disk writes.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--pair', required=True)
    parser.add_argument('--disk-a', required=True, type=Path)
    parser.add_argument('--disk-b', required=True, type=Path)
    args = parser.parse_args()
    require(re.fullmatch(r'[1-9][0-9]*', args.pair), 'PAIR must be a positive integer')
    require(os.geteuid() == 0, 'Run through make write-usb with sudo on the host')
    for tool in ('lsblk', 'losetup', 'zpool', 'dd', 'sgdisk', 'sync', 'partprobe'):
        require(shutil.which(tool) is not None, f'Missing host tool: {tool}')
    with open('/run/lock/level1vault-write-usb.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        write_pair(args.out.resolve(), args.pair, args.disk_a, args.disk_b)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'USB writing stopped: {error}. If any write started, inspect both disks before retrying.')
