import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

class UmountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.bin = self.base/'bin'
        self.bin.mkdir()
        self.log = self.base/'calls'
        self.env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}', LOG=str(self.log))
        text = (ROOT/'runtime/workspace-umount').read_text().replace('/run/lock/level1vault-workspace.lock', str(self.base/'lock'))
        self.script = self.base/'umount'
        text = text.replace('/usr/local/share/level1vault/source-commit', str(self.base / 'live-marker'))
        self.script.write_text(text)
        self.tool('id', 'echo 0')
        self.tool('zfs', 'echo 1')
        for name in ('flock', 'modprobe'):
            self.tool(name, ':')
        self.tool('zpool', '''case "$1" in
list) if [ "${LIST_FAIL:-0}" = 1 ]; then exit 1; fi
      if [ "${IMPORTED:-1}" = 1 ]; then echo workspace-1; fi;;
sync) exit "${SYNC_FAIL:-0}";;
export) exit "${EXPORT_FAIL:-0}";;
esac''')

    def tool(self, name, body):
        file = self.bin/name
        file.write_text(f'#!/bin/sh\nset -eu\necho "{name} $*" >> "$LOG"\n'+body+'\n')
        file.chmod(0o755)

    def run_helper(self):
        return subprocess.run(['sh', str(self.script)], env=self.env, capture_output=True, text=True)

    def calls(self):
        return self.log.read_text()

    def test_sync_precedes_nonforced_export(self):
        self.assertEqual(self.run_helper().returncode, 0)
        calls = self.calls()
        self.assertLess(calls.index('zpool sync workspace-1'), calls.index('zpool export workspace-1'))
        self.assertNotIn(' -f', calls)
        self.assertNotIn('poweroff', calls)
        self.assertNotIn('zpool import', calls)

    def test_already_exported_is_idempotent(self):
        self.env['IMPORTED'] = '0'
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertNotIn('zpool export', self.calls())

    def test_sync_failure_stops_export(self):
        self.env['SYNC_FAIL'] = '1'
        self.assertNotEqual(self.run_helper().returncode, 0)
        self.assertNotIn('zpool export', self.calls())

    def test_busy_pool_fails_without_force(self):
        self.env['EXPORT_FAIL'] = '1'
        r = self.run_helper()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('retry before poweroff', r.stderr)
        self.assertNotIn(' -f', self.calls())

    def test_list_failure_is_not_reported_as_safe(self):
        self.env['LIST_FAIL'] = '1'
        self.assertNotEqual(self.run_helper().returncode, 0)
        self.assertNotIn('zpool export', self.calls())


    def test_live_home_detach_precedes_export_and_failure_blocks_it(self):
        (self.base / 'live-marker').touch()
        (self.base / 'workspace-home').write_text(
            "import os,sys; open(os.environ['LOG'], 'a').write('home ' + ' '.join(sys.argv[1:]) + '\\n'); "
            "sys.exit(int(os.environ.get('HOME_FAIL', '0')))\n")
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertLess(self.calls().index('home detach workspace-1'), self.calls().index('zpool export'))
        self.log.unlink()
        self.env['HOME_FAIL'] = '1'
        self.assertNotEqual(self.run_helper().returncode, 0)
        self.assertNotIn('zpool export', self.calls())
