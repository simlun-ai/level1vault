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
POOLS = ['workspace', 'workspace-backup1', 'workspace-backup2']


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


def prepare(base, pair, receive_only=False):
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
    if receive_only:
        cmd.append('--receive-only')
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
            for pair, pool in zip(['primary', 'backup1', 'backup2'], POOLS):
                out = prepare(base, pair)
                owned.append(pool)
                for side in ['a', 'b']:
                    loops.append(run('losetup', '--find', '--show', '--partscan', str(out/f'usb-{side}.img')))
                command('udevadm', 'settle')
                command('zpool', 'import', '-N', '-d', '/dev', '-o', 'cachefile=none', pool)
            # Wrong keys fail closed; real stdin unlock and UID/GID permissions.
            helper('workspace-mount', input='incorrect-passphrase\n', ok=False)
            assert not os.path.ismount('/mnt/workspace')
            helper('workspace-mount', input=KEY + '\n')
            stat = os.stat('/mnt/workspace')
            assert (stat.st_uid, stat.st_gid, stat.st_mode & 0o777) == (2000, 2000, 0o750)
            command('setpriv', '--reuid=2000', '--regid=2000', '--clear-groups',
                    'sh', '-c', 'printf first > /mnt/workspace/user-file')
            command('zfs', 'unmount', 'workspace/data')
            command('zfs', 'unload-key', 'workspace/data')
            helper('workspace-sync', 'backup1')  # full raw send without loaded source key
            helper('workspace-mount', input=KEY + '\n')
            assert get('workspace-backup1/data', 'readonly') == 'on'
            assert get('workspace-backup1/data', 'keystatus') == 'unavailable'
            assert get('workspace-backup1/data', 'mounted') == 'no'
            Path('/mnt/workspace/user-file').write_text('second')
            helper('workspace-sync', 'backup1')
            # backup2 gets its initial full stream; interrupt it first.
            with open('/mnt/workspace/random', 'wb') as f:
                f.write(os.urandom(32 * 1024 * 1024))
            real_transfer = sync.transfer
            sync.transfer = interrupted_transfer
            try:
                sync.synchronize('backup2')
                raise AssertionError('Expected injected interruption')
            except RuntimeError as e:
                assert str(e) == 'Injected cable interruption', str(e)
            finally:
                sync.transfer = real_transfer
            helper('workspace-sync', 'backup2')
            assert get('workspace-backup2/data', 'receive_resume_token') == '-'
            # Interrupt and resume an incremental independently on backup1.
            sync.transfer = interrupted_transfer
            try:
                sync.synchronize('backup1')
                raise AssertionError('Expected injected interruption')
            except RuntimeError as e:
                assert str(e) == 'Injected cable interruption', str(e)
            finally:
                sync.transfer = real_transfer
            helper('workspace-sync', 'backup1')
            command('zfs', 'create', '-u', '-o', 'canmount=off', '-o', 'mountpoint=none', 'workspace/data/child')
            helper('workspace-sync', 'backup1', ok=False)
            command('zfs', 'destroy', 'workspace/data/child')
            # A later backup1 update must preserve backup2's distinct base hold.
            Path('/mnt/workspace/user-file').write_text('third')
            helper('workspace-sync', 'backup1')
            held = run('zfs', 'holds', '-H', '-r', *[line for line in
                       run('zfs', 'list', '-H', '-o', 'name', '-t', 'snapshot', '-r', 'workspace/data').splitlines()])
            for pool in POOLS[1:]:
                guid = run('zpool', 'get', '-H', '-o', 'value', 'guid', pool)
                assert 'level1vault-' + guid in held
            helper('workspace-sync', 'backup2')
            # Read-only recovery boot workflow, same passphrase and file owner.
            command('zfs', 'unmount', 'workspace/data')
            helper('workspace-mount', 'backup1', input=KEY + '\n')
            assert Path('/mnt/workspace/user-file').read_text() == 'third'
            assert os.stat('/mnt/workspace').st_uid == 2000
            assert subprocess.run(['touch', '/mnt/workspace/should-fail']).returncode != 0
            # Receiving while target mounted is refused, then unloaded on retry.
            helper('workspace-sync', 'backup1', ok=False)
            command('zfs', 'unmount', 'workspace-backup1/data')
            helper('workspace-sync', 'backup1')
            # Divergent destination snapshots must never be rolled back.
            command('zfs', 'snapshot', 'workspace-backup1/data@unexpected-local')
            helper('workspace-sync', 'backup1', ok=False)
            command('zfs', 'destroy', 'workspace-backup1/data@unexpected-local')
            # Recovery uses a new primary pool and a full raw receive.
            command('zpool', 'destroy', 'workspace')  # only this test's new file vdevs
            restore = base/'restore'
            restore.mkdir()
            out = prepare(restore, 'primary', receive_only=True)
            for side in ['a', 'b']:
                loops.append(run('losetup', '--find', '--show', '--partscan', str(out/f'usb-{side}.img')))
            command('udevadm', 'settle')
            command('zpool', 'import', '-N', '-d', '/dev', '-o', 'cachefile=none', 'workspace')
            sync.transfer = interrupted_transfer
            try:
                sync.synchronize('backup1', restore=True)
                raise AssertionError('Expected injected interruption')
            except RuntimeError as e:
                assert str(e) == 'Injected cable interruption', str(e)
            finally:
                sync.transfer = real_transfer
            helper('workspace-sync', '--restore', 'backup1')
            helper('workspace-mount', input=KEY + '\n')
            assert Path('/mnt/workspace/user-file').read_text() == 'third'
            assert os.stat('/mnt/workspace').st_uid == 2000
            command('setpriv', '--reuid=2000', '--regid=2000', '--clear-groups',
                    'touch', '/mnt/workspace/restored-writable')
            # Old backups are intentionally bound to the old primary GUID.
            helper('workspace-sync', 'backup2', ok=False)
            helper('workspace-umount')
            assert not set(run('zpool', 'list', '-H', '-o', 'name').splitlines()).intersection(POOLS)
            print('Real ZFS encrypted setup, ownership, full/incremental/resume/restore checks passed.')
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
