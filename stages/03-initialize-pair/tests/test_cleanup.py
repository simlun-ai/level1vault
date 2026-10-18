#!/usr/bin/env python3
"""Behavior tests with mocked system tools; no mounts, ZFS, or privileged IO."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]

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


    def calls(self):
        return self.log.read_text() if self.log.exists() else ''


    def test_cleanup_retains_loops_when_export_fails(self):
        text = (ROOT / 'stages/03-initialize-pair/initialize.sh').read_text()
        text = text.replace('$(dirname -- "${BASH_SOURCE[0]}")/../../common/artifact_owner.py', str(ROOT / 'common/artifact_owner.py'))
        cleanup = text.split('cleanup() {', 1)[1].split('\ntrap cleanup EXIT', 1)[0]
        for export_rc in (0, 1):
            with self.subTest(export_rc=export_rc):
                self.log.unlink(missing_ok=True)
                self.tool('zpool', f'echo "zpool $*" >> "$LOG"\nexit {export_rc}')
                self.tool('losetup', 'echo "losetup $*" >> "$LOG"')
                script = self.base / 'cleanup.sh'
                script.write_text('#!/bin/bash\nA=/dev/mock-a B=/dev/mock-b POOL_CREATED=1 POOL=level1-workspace-1\n'
                                  'OUT_DIR="$PWD"\ncleanup() {' + cleanup + '\ntrue\ncleanup\n')
                result = subprocess.run(['bash', str(script)], env=self.env, cwd=self.base,
                                        capture_output=True, text=True)
                self.assertEqual('losetup -d' in self.calls(), export_rc == 0)
                self.assertEqual(result.returncode, export_rc)
