#!/usr/bin/env python3
"""Build a fresh bootable, empty replacement without touching USB devices or pools."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'common'))
from member_recovery import command, output, partition_table, require, validate_record


def prepare(original, recovery, pair, member):
    # Preserve the current membership record before building anything.
    require(re.fullmatch(r'[1-9][0-9]*', pair) is not None and member in ('a', 'b'), 'Invalid pair or member')
    require(not recovery.exists() and not recovery.is_symlink(), 'Use a new RECOVERY_DIR')
    require((original / 'READY').is_file() and not (original / 'INITIALIZING').exists(),
            'OUT must identify the original initialized pair')
    info = dict(line.split('=', 1) for line in (original / 'WORKSPACE-INFO.txt').read_text().splitlines() if '=' in line)
    require(info.get('pair') == pair and info.get('pool') == f'level1-workspace-{pair}', 'OUT belongs to a different pair')
    old = json.loads((original / 'images.json').read_text())
    require(old['format'] == 1, 'Unsupported original manifest')
    # Read actual partition bounds, including custom layouts, without attaching loops.
    old_table = partition_table(original / f'usb-{member}.img')
    minimum = old_table['partitions'][1]['size'] * 512
    require(old_table['partitions'][1]['uuid'].lower() == old['images'][member]['zfs_partuuid'],
            'Original image and manifest disagree')

    # Build the current committed OS so the new drive contains workspace-replace.
    env = dict(os.environ, PAIR=pair)
    subprocess.run(['bash', str(ROOT / 'pipeline/buildkit.sh'), 'build', str(recovery.resolve())], check=True, env=env)
    out = recovery / 'pairs' / pair
    require((out / 'IMAGES-PREPARED').is_file() and not (out / 'READY').exists(), 'Expected uninitialized output')
    new = json.loads((out / 'images.json').read_text())
    image = out / f'usb-{member}.img'
    table = partition_table(image)
    require(table['partitions'][1]['size'] * 512 >= minimum,
            'New workspace partition is too small; custom enlarged layouts require manual preparation')
    record = validate_record(dict(format=1, pair=pair, member=member, pool_guid=info['pool_guid'],
        failed_partuuid=old['images'][member]['zfs_partuuid'],
        survivor_partuuid=old['images']['b' if member == 'a' else 'a']['zfs_partuuid'],
        new_partuuid=new['images'][member]['zfs_partuuid'], esp_uuid=new['images'][member]['esp_uuid'],
        minimum_bytes=minimum))

    # Embed non-secret recovery metadata in the EFI filesystem before USB readback.
    record_path = out / 'member-recovery.json'
    record_path.write_text(json.dumps(record, indent=2) + '\n')
    esp_image = f'{image}@@{table["partitions"][0]["start"] * 512}'
    command('mcopy', '-i', esp_image, record_path, '::/member-recovery.json')
    require(json.loads(output('mtype', '-i', esp_image, '::/member-recovery.json')) == record, 'Embedded record differs')
    for name in ('images.json', 'WORKSPACE-INFO.txt'):
        shutil.copyfile(original / name, out / ('original-' + name))
    (out / 'REPLACEMENT-READY').touch()
    print(f'Prepared replacement {pair}{member.upper()}: {out}')
    print(f'Next: disconnect the survivor, identify the blank USB, then make write-replacement PAIR={pair} MEMBER={member.upper()} RECOVERY_DIR={recovery} DISK=/dev/sda')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', required=True, type=Path)
    parser.add_argument('--recovery-dir', required=True, type=Path)
    parser.add_argument('--pair', required=True)
    parser.add_argument('--member', required=True, choices=('a', 'b'))
    args = parser.parse_args()
    prepare(args.original, args.recovery_dir, args.pair, args.member)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'Replacement preparation stopped: {error}. Preserve diagnostics and use a new RECOVERY_DIR.')
