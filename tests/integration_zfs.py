#!/usr/bin/env python3
"""Destructive ONLY to newly created files/pools on a disposable CI VM.

Do not run on a workstation: requires LEVEL1VAULT_DISPOSABLE_TEST_VM=1 and
refuses any pre-existing imported level1vault pool. No physical disk writes.
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
KEY = 'integration-test-passphrase'
PAIRS = ['1', '2', '3', '10']
POOLS = ['workspace-' + pair for pair in PAIRS]


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def command(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def get(dataset, name):
    return run('zfs', 'get', '-H', '-p', '-o', 'value', name, dataset)


def helper(name, *args, input=None, ok=True):
    r = subprocess.run([str(ROOT/'live'/name), *args], text=True, input=input, capture_output=True)
    if ok:
        assert r.returncode == 0, r.stdout + r.stderr
    else:
        assert r.returncode != 0, r.stdout + r.stderr
    return r


def prepare(base, pair, active=False):
    out = base/pair
    out.mkdir()
    images = {}
    for side in ['a', 'b']:
        image = out/f'usb-{side}.img'
        command('truncate', '-s', '512M', str(image))
        command('sgdisk', '--clear', '-n', '1:2048:+8M', '-t', '1:ef00',
                '-n', '2:0:0', '-t', '2:bf01', str(image), stdout=subprocess.DEVNULL)
        part_uuid = next(line.split(': ', 1)[1].strip().lower() for line in
                         run('sgdisk', '-i', '2', str(image)).splitlines() if line.startswith('Partition unique GUID:'))
        images[side] = {'file': image.name, 'zfs_partuuid': part_uuid}
    (out/'images.json').write_text(json.dumps({'format': 1, 'images': images}))
    with (out/'PREPARED-SHA256SUMS').open('w') as f:
        for name in ['usb-a.img', 'usb-b.img', 'images.json']:
            f.write(hashlib.sha256((out/name).read_bytes()).hexdigest() + '  ' + name + '\n')
    (out/'IMAGES-PREPARED').touch()
    cmd = ['bash', str(ROOT/'03-initialize-workspace.sh'), str(out), pair]
    if active:
        cmd.append('--active')
    command(*cmd, input=KEY + '\n', text=True)
    assert (out/'READY').exists()
    return out


def interrupted_transfer(send, destination, readonly):
    # Produce a genuine resumable receive by closing its input mid-stream.
    sender = subprocess.Popen(send, stdout=subprocess.PIPE)
    receiver = subprocess.Popen(['zfs', 'receive', '-u', '-s', '-o', 'readonly=' + readonly,
                                 '-o', 'canmount=noauto', '-o', 'mountpoint=/mnt/workspace',
                                 '-o', 'org.openzfs.systemd:ignore=on', destination], stdin=subprocess.PIPE)
    try:
        chunk = sender.stdout.read(2 * 1024 * 1024)
        receiver.stdin.write(chunk)
        receiver.stdin.close()
        assert receiver.wait() != 0
    finally:
        sender.stdout.close()
        sender.wait()
    assert get(destination, 'receive_resume_token') != '-'
    raise RuntimeError('Injected cable interruption')


def main():
    assert os.geteuid() == 0 and os.environ.get('LEVEL1VAULT_DISPOSABLE_TEST_VM') == '1'
    command('modprobe', 'zfs')
    assert not set(run('zpool', 'list', '-H', '-o', 'name').splitlines()).intersection(POOLS)
    assert not os.path.ismount('/mnt/workspace')
    loader = importlib.machinery.SourceFileLoader('workspace_sync', str(ROOT/'live/workspace-sync'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    sync = importlib.util.module_from_spec(spec)
    loader.exec_module(sync)
    loops = []
    owned = []
    with tempfile.TemporaryDirectory(prefix='level1vault-zfs-') as tmp:
        base = Path(tmp)
        try:
            for pair, pool in zip(PAIRS, POOLS):
                out = prepare(base, pair, active=(pair == '1'))
                owned.append(pool)
                for side in ['a', 'b']:
                    loops.append(run('losetup', '--find', '--show', '--partscan', str(out/f'usb-{side}.img')))
                command('udevadm', 'settle')
                command('zpool', 'import', '-N', '-d', '/dev', '-o', 'cachefile=none', pool)
            helper('workspace-mount', '1', input='incorrect-passphrase\n', ok=False)
            assert not os.path.ismount('/mnt/workspace')
            helper('workspace-mount', '1', input=KEY + '\n')
            stat = os.stat('/mnt/workspace')
            assert (stat.st_uid, stat.st_gid, stat.st_mode & 0o777) == (2000, 2000, 0o750)
            command('setpriv', '--reuid=2000', '--regid=2000', '--clear-groups',
                    'sh', '-c', 'printf first > /mnt/workspace/user-file')
            command('zfs', 'unmount', 'workspace-1/data')
            command('zfs', 'unload-key', 'workspace-1/data')
            helper('workspace-sync', '1', '2', '10')  # shared full checkpoint, source key unloaded
            assert get('workspace-2/data', 'readonly') == 'on'
            assert get('workspace-2/data', 'keystatus') == 'unavailable'
            helper('workspace-mount', '1', input=KEY + '\n')
            Path('/mnt/workspace/user-file').write_text('second')
            helper('workspace-sync', '1', '2')
            with open('/mnt/workspace/random', 'wb') as f:
                f.write(os.urandom(32 * 1024 * 1024))
            real_transfer = sync.transfer
            # A new pair's full raw receive is interrupted and resumed.
            sync.transfer = interrupted_transfer
            try:
                sync.synchronize('1', '3')
                raise AssertionError('Expected injected interruption')
            except RuntimeError as e:
                assert str(e) == 'Injected cable interruption', str(e)
            finally:
                sync.transfer = real_transfer
            helper('workspace-sync', '1', '3')
            assert get('workspace-3/data', 'receive_resume_token') == '-'
            # A later incremental receive resumes independently.
            sync.transfer = interrupted_transfer
            try:
                sync.synchronize('1', '2')
                raise AssertionError('Expected injected interruption')
            except RuntimeError as e:
                assert str(e) == 'Injected cable interruption', str(e)
            finally:
                sync.transfer = real_transfer
            helper('workspace-sync', '1', '2')
            command('zfs', 'create', '-u', '-o', 'canmount=off', '-o', 'mountpoint=none', 'workspace-1/data/child')
            helper('workspace-sync', '1', '2', ok=False)
            command('zfs', 'destroy', 'workspace-1/data/child')
            Path('/mnt/workspace/user-file').write_text('third')
            helper('workspace-sync', '1', '2', '3', '10')
            checkpoint = sync.snapshots('workspace-1/data')[-1]
            for pair in PAIRS[1:]:
                latest = sync.snapshots('workspace-' + pair + '/data')[-1]
                assert latest[1] == checkpoint[1], (pair, latest, checkpoint)
            held = run('zfs', 'holds', '-H', '-r', *[name for name, _ in sync.snapshots('workspace-1/data')])
            for pool in POOLS[1:]:
                assert 'level1vault-' + run('zpool', 'get', '-H', '-o', 'value', 'guid', pool) in held
            # Reusing an explicit checkpoint when rotating pairs separately.
            helper('workspace-sync', '--snapshot', checkpoint[0].split('@')[1], '1', '10')
            helper('workspace-sync', '2', '3', ok=False)  # inactive source
            helper('workspace-activate', '2', ok=False)  # no fencing acknowledgement
            helper('workspace-activate', '--next', '1', '--fenced', ok=False)  # old active still imported
            Path('/mnt/workspace/former-only').write_text('preserve on fenced owner')
            command('zfs', 'unmount', 'workspace-1/data')
            helper('workspace-mount', '2', input=KEY + '\n')
            assert Path('/mnt/workspace/user-file').read_text() == 'third'
            assert subprocess.run(['touch', '/mnt/workspace/should-fail']).returncode != 0
            command('zfs', 'unmount', 'workspace-2/data')
            # Fence the former active owner. Names/identities are never changed.
            command('zpool', 'export', 'workspace-1')
            helper('workspace-activate', '--next', '1', '--fenced')
            assert get('workspace-2', 'org.level1vault:state') == 'active'
            assert get('workspace-10', 'org.level1vault:state') == 'replica'
            helper('workspace-activate', '2', '--fenced')  # idempotent
            helper('workspace-mount', '2', input=KEY + '\n')
            command('setpriv', '--reuid=2000', '--regid=2000', '--clear-groups',
                    'sh', '-c', 'printf fourth > /mnt/workspace/user-file')
            # Binding changes are explicit; verified common history allows
            # incrementals from the newly active pair without destroying replicas.
            helper('workspace-sync', '2', '3', ok=False)
            dataset_guid = get('workspace-3/data', 'guid')
            helper('workspace-sync', '--rebind', '2', '3', '10')
            assert get('workspace-3/data', 'guid') == dataset_guid
            assert get('workspace-3', 'org.level1vault:source-pool-guid') == run('zpool', 'get', '-H', '-o', 'value', 'guid', 'workspace-2')
            command('zfs', 'unmount', 'workspace-2/data')
            command('zpool', 'export', 'workspace-2')
            # A second ordered failover selects 3 rather than lexicographic 10.
            helper('workspace-activate', '--next', '2', '--fenced')
            assert get('workspace-3', 'org.level1vault:state') == 'active'
            helper('workspace-mount', '3', input=KEY + '\n')
            assert Path('/mnt/workspace/user-file').read_text() == 'fourth'
            assert os.stat('/mnt/workspace').st_uid == 2000
            Path('/mnt/workspace/user-file').write_text('fifth')
            helper('workspace-sync', '--rebind', '3', '10')
            # A mounted destination or divergent local snapshot is never forced.
            command('zfs', 'unmount', 'workspace-3/data')
            helper('workspace-mount', '10', input=KEY + '\n')
            helper('workspace-sync', '3', '10', ok=False)
            command('zfs', 'unmount', 'workspace-10/data')
            command('zfs', 'snapshot', 'workspace-10/data@unexpected-local')
            helper('workspace-sync', '3', '10', ok=False)
            command('zfs', 'destroy', 'workspace-10/data@unexpected-local')
            helper('workspace-sync', '3', '10')
            # A returning former owner can contain newer unsnapshotted writes.
            # Even after manual demotion those must never be overwritten.
            command('zpool', 'import', '-N', '-d', '/dev', '-o', 'cachefile=none', 'workspace-1')
            helper('workspace-mount', '3', ok=False)  # two observable active owners
            command('zfs', 'set', 'readonly=on', 'workspace-1/data')
            command('zfs', 'set', 'org.level1vault:state=replica', 'workspace-1')
            old_guid = get('workspace-1/data', 'guid')
            helper('workspace-sync', '--rebind', '3', '1', ok=False)
            assert get('workspace-1/data', 'guid') == old_guid
            helper('workspace-mount', '1', input=KEY + '\n')
            assert Path('/mnt/workspace/user-file').read_text() == 'third'
            assert Path('/mnt/workspace/former-only').read_text() == 'preserve on fenced owner'
            helper('workspace-umount')
            assert not set(run('zpool', 'list', '-H', '-o', 'name').splitlines()).intersection(POOLS)
            print('Real encrypted n-pair sync, resume, shared checkpoints and ordered in-place failovers passed.')
        finally:
            # This VM is disposable; destroy only pools this test created. If
            # export fails, keep files/loops and fail rather than force detaching.
            for pool in owned:
                if pool in run('zpool', 'list', '-H', '-o', 'name').splitlines():
                    command('zpool', 'export', pool)
            for loop in loops:
                command('losetup', '-d', loop)


if __name__ == '__main__':
    main()
