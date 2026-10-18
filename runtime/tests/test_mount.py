#!/usr/bin/env python3
"""Behavior tests with mocked system tools; no mounts, ZFS, or privileged IO."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.log = self.base / 'calls'
        self.env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}', LOG=str(self.log))


    def tool(self, name, body):
        file = self.bin / name
        file.write_text('#!/bin/sh\nset -eu\n' + body + '\n')
        file.chmod(0o755)


    def run_script(self, script):
        return subprocess.run(['sh', str(script), '1'], env=self.env, capture_output=True, text=True)


    def workspace(self, *, imported=False, mounted=False, source='workspace-1/data',
                  mountpoint=None, canmount='noauto', import_fails=False, encrypted=True, key_fails=False):
        mp = self.base / 'workspace'
        mp.mkdir()
        lock = self.base / 'lock'
        hostid = self.base / 'hostid'
        hostid.write_bytes(b'1234')
        script = self.base / 'workspace-mount'
        text = (ROOT / 'runtime/workspace-mount').read_text()
        text = text.replace('/mnt/workspace', str(mp)).replace('/run/lock/level1vault-workspace.lock', str(lock)).replace('/etc/hostid', str(hostid))
        script.write_text(text)
        for name in ('flock', 'modprobe', 'udevadm', 'zgenhostid'):
            self.tool(name, f'echo "{name} $*" >> "$LOG"')
        self.tool('id', 'echo 0')
        self.tool('zpool', f'''echo "zpool $*" >> "$LOG"
case "$1" in
list) if [ "$*" = 'list -H -o name' ]; then echo workspace-1; exit 0; fi
      exit {0 if imported else 1};;
import) exit {1 if import_fails else 0};;
status) exit 0;;
esac''')
        self.tool('zfs', f'''echo "zfs $*" >> "$LOG"
case "$1" in
get) case "$5" in
mountpoint) echo '{mountpoint or mp}';;
canmount) echo '{canmount}';;
mounted) echo no;;
org.level1vault:pair) echo 1;;
org.level1vault:state) echo active;;
readonly) echo off;;
encryption) echo {'aes-256-gcm' if encrypted else 'off'};;
encryptionroot) echo workspace-1/data;;
keyformat) echo passphrase;;
keystatus) echo unavailable;;
esac;;
load-key) exit {1 if key_fails else 0};;
mount) exit 0;;
esac''')
        self.tool('mountpoint', f'exit {0 if mounted else 1}')
        self.tool('findmnt', f'echo "findmnt $*" >> "$LOG"\necho "{source}"')
        return script


    def calls(self):
        return self.log.read_text() if self.log.exists() else ''


    def test_manual_import_and_mount(self):
        result = self.run_script(self.workspace())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('zpool import -N -d /dev/disk/by-partuuid -o cachefile=none workspace-1', self.calls())
        self.assertIn('zfs mount workspace-1/data', self.calls())
        self.assertNotIn('import -f', self.calls())


    def test_key_failure_never_mounts(self):
        result = self.run_script(self.workspace(key_fails=True))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('zfs load-key -L prompt workspace-1/data', self.calls())
        self.assertNotIn('zfs mount ', self.calls())


    def test_unencrypted_workspace_is_rejected(self):
        result = self.run_script(self.workspace(encrypted=False))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('zfs load-key', self.calls())
        self.assertNotIn('zfs mount ', self.calls())


    def test_already_mounted_is_idempotent(self):
        result = self.run_script(self.workspace(imported=True, mounted=True))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('zpool import', self.calls())
        self.assertNotIn('zfs mount ', self.calls())


    def test_active_pool_import_failure_stops_before_mount(self):
        result = self.run_script(self.workspace(import_fails=True))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('zfs mount ', self.calls())


    def test_wrong_mountpoint_is_rejected(self):
        result = self.run_script(self.workspace(mountpoint='/unexpected'))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('zfs mount ', self.calls())


    def test_automatic_mount_property_is_rejected(self):
        result = self.run_script(self.workspace(canmount='on'))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('zfs mount ', self.calls())


    def test_foreign_mount_is_rejected(self):
        result = self.run_script(self.workspace(imported=True, mounted=True, source='/dev/foreign'))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('zfs mount ', self.calls())


    def test_nonempty_mountpoint_is_rejected(self):
        script = self.workspace()
        (self.base / 'workspace/file').write_text('do not hide me')
        result = self.run_script(script)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('zfs mount ', self.calls())


    def test_nonroot_is_rejected_before_import(self):
        script = self.workspace()
        self.tool('id', 'echo 1000')
        result = self.run_script(script)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('zpool ', self.calls())


    def test_omitted_pair_uses_discovery_before_import(self):
        script = self.workspace()
        helper = self.base / 'workspace-discover'
        helper.write_text("print('1')\n")
        result = subprocess.run(['sh', str(script)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Selected workspace-1', result.stdout)
        self.assertIn('zpool import -N', self.calls())

    def test_failed_discovery_never_imports_or_mounts(self):
        script = self.workspace()
        (self.base / 'workspace-discover').write_text("raise SystemExit('Connect both members or specify a pair')\n")
        result = subprocess.run(['sh', str(script)], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('zpool import', self.calls())
        self.assertNotIn('zfs mount', self.calls())
