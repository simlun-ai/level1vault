#!/usr/bin/env python3
"""Behavior tests with mocked system tools; no mounts, ZFS, or privileged IO."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

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
        return subprocess.run(['sh', str(script)], env=self.env, capture_output=True, text=True)

    def workspace(self, *, imported=False, mounted=False, source='workspace/data',
                  mountpoint=None, canmount='noauto', import_fails=False):
        mp = self.base / 'workspace'
        mp.mkdir()
        lock = self.base / 'lock'
        hostid = self.base / 'hostid'
        hostid.write_bytes(b'1234')
        script = self.base / 'mount-workspace'
        text = (ROOT / 'live/mount-workspace').read_text()
        text = text.replace('/mnt/workspace', str(mp)).replace('/run/lock/level1vault-workspace.lock', str(lock)).replace('/etc/hostid', str(hostid))
        script.write_text(text)
        for name in ('flock', 'modprobe', 'udevadm', 'zgenhostid'):
            self.tool(name, f'echo "{name} $*" >> "$LOG"')
        self.tool('id', 'echo 0')
        self.tool('zpool', f'''echo "zpool $*" >> "$LOG"
case "$1" in
list) exit {0 if imported else 1};;
import) exit {1 if import_fails else 0};;
status) exit 0;;
esac''')
        self.tool('zfs', f'''echo "zfs $*" >> "$LOG"
case "$1" in
get) case "$5" in
mountpoint) echo '{mountpoint or mp}';;
canmount) echo '{canmount}';;
mounted) echo no;;
esac;;
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
        self.assertIn('zpool import -N -d /dev/disk/by-partuuid -o cachefile=none workspace', self.calls())
        self.assertIn('zfs mount workspace/data', self.calls())
        self.assertNotIn('import -f', self.calls())

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

    def test_configure_installs_sources_and_refuses_overwrite(self):
        self.tool('lb', 'printf "%s\\n" "$@" >> "$LOG"')
        first = subprocess.run(['bash', str(ROOT / '01-configure-live.sh')], cwd=self.base,
                               env=self.env, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        target = self.base / 'config/includes.chroot/usr/local/sbin/mount-workspace'
        self.assertEqual(target.read_bytes(), (ROOT / 'live/mount-workspace').read_bytes())
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        self.assertIn('linux-image linux-headers', self.calls())
        self.assertIn('syslinux grub-efi', self.calls())
        again = subprocess.run(['bash', str(ROOT / '01-configure-live.sh')], cwd=self.base,
                               env=self.env, capture_output=True, text=True)
        self.assertNotEqual(again.returncode, 0)

    def test_cleanup_retains_loops_when_export_fails(self):
        text = (ROOT / '03-initialize-workspace.sh').read_text()
        cleanup = text.split('cleanup() {', 1)[1].split('\ntrap cleanup EXIT', 1)[0]
        for export_rc in (0, 1):
            with self.subTest(export_rc=export_rc):
                self.log.unlink(missing_ok=True)
                self.tool('zpool', f'echo "zpool $*" >> "$LOG"\nexit {export_rc}')
                self.tool('losetup', 'echo "losetup $*" >> "$LOG"')
                script = self.base / 'cleanup.sh'
                script.write_text('#!/bin/bash\nA=/dev/mock-a B=/dev/mock-b POOL_CREATED=1\n'
                                  'OUT_DIR="$PWD"\ncleanup() {' + cleanup + '\ntrue\ncleanup\n')
                result = subprocess.run(['bash', str(script)], env=self.env, cwd=self.base,
                                        capture_output=True, text=True)
                self.assertEqual('losetup -d' in self.calls(), export_rc == 0)
                self.assertEqual(result.returncode, export_rc)

    def test_container_wrapper_keeps_host_kernel_and_zfs_out(self):
        self.tool('docker', 'echo "docker $*" >> "$LOG"')
        self.tool('uname', 'echo x86_64')
        output = self.base / 'build'
        result = subprocess.run(['bash', str(ROOT / 'build-container.sh'), str(output)],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--privileged', self.calls())
        self.assertIn('target=/src,readonly', self.calls())
        self.assertIn('bind-propagation=rprivate', self.calls())
        self.assertNotIn('/lib/modules', self.calls())
        self.assertNotIn('/run/udev', self.calls())
        self.assertNotIn('zpool', self.calls())
        again = subprocess.run(['bash', str(ROOT / 'build-container.sh'), str(output)],
                               env=self.env, capture_output=True, text=True)
        self.assertNotEqual(again.returncode, 0)

if __name__ == '__main__':
    unittest.main(verbosity=2)
