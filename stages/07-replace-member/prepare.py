#!/usr/bin/env python3
"""Build a fresh bootable, empty replacement without touching USB devices or pools."""
import argparse
import json
import os
from pathlib import Path
import re
import copy
import uuid
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'common'))
from member_recovery import command, output, partition_table, require, validate_record
from pair_records import locked_pair, records_root, require_separate, validate_membership, write_json


def prepare(membership, recovery, pair, member):
    require_separate(records_root(), recovery)
    # Preserve the current membership record before building anything.
    require(re.fullmatch(r'[1-9][0-9]*', pair) is not None and member in ('a', 'b'), 'Invalid pair or member')
    require(not recovery.exists() and not recovery.is_symlink(), 'Use a new RECOVERY_DIR')
    old = validate_membership(json.loads(membership.read_text()), pair)
    minimum = max(row['minimum_bytes'] for row in old['members'].values())

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
    record = validate_record(dict(format=1, pair=pair, member=member, pool_guid=old['pool_guid'],
        failed_partuuid=old['members'][member]['zfs_partuuid'],
        survivor_partuuid=old['members']['b' if member == 'a' else 'a']['zfs_partuuid'],
        new_partuuid=new['images'][member]['zfs_partuuid'], esp_uuid=new['images'][member]['esp_uuid'],
        minimum_bytes=minimum))

    # Embed non-secret recovery metadata in the EFI filesystem before USB readback.
    record_path = out / 'member-recovery.json'
    record_path.write_text(json.dumps(record, indent=2) + '\n')
    esp_image = f'{image}@@{table["partitions"][0]["start"] * 512}'
    command('mcopy', '-i', esp_image, record_path, '::/member-recovery.json')
    require(json.loads(output('mtype', '-i', esp_image, '::/member-recovery.json')) == record, 'Embedded record differs')
    # Keep a durable proposal; only acknowledge it after successful live recovery.
    after = copy.deepcopy(old)
    after['members'][member] = dict(zfs_partuuid=record['new_partuuid'],
        esp_uuid=record['esp_uuid'], minimum_bytes=table['partitions'][1]['size'] * 512)
    validate_membership(after, pair)
    replacement_id = str(uuid.uuid4())
    with locked_pair(records_root(), pair) as directory:
        require(json.loads((directory / 'current.json').read_text()) == old, 'Current membership changed during preparation')
        history = directory / 'replacements'
        history.mkdir(exist_ok=True)
        write_json(history / (replacement_id + '.json'), dict(before=old, after=after, recovery=record))
    (out / 'replacement-id').write_text(replacement_id + '\n')
    print(f'After live recovery and read-check: make confirm-replacement PAIR={pair} REPLACEMENT={replacement_id} CONFIRMED=yes')
    (out / 'REPLACEMENT-READY').touch()
    print(f'Prepared replacement {pair}{member.upper()}: {out}')
    print(f'Next: disconnect the survivor, identify the blank USB, then make write-replacement PAIR={pair} MEMBER={member.upper()} RECOVERY_DIR={recovery} DISK=/dev/sda')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--membership', required=True, type=Path)
    parser.add_argument('--recovery-dir', required=True, type=Path)
    parser.add_argument('--pair', required=True)
    parser.add_argument('--member', required=True, choices=('a', 'b'))
    args = parser.parse_args()
    prepare(args.membership, args.recovery_dir, args.pair, args.member)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'Replacement preparation stopped: {error}. Preserve diagnostics and use a new RECOVERY_DIR.')
