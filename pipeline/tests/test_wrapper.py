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


    def calls(self):
        return self.log.read_text() if self.log.exists() else ''


    def test_container_wrapper_keeps_host_kernel_and_zfs_out(self):
        self.tool('docker', '''echo "docker $*" >> "$LOG"
case "$1" in
context) echo unix:///var/run/docker.sock;;
buildx) case "$2" in inspect) echo "Driver: docker-container";; esac;;
info) echo '{"OSType":"linux","Architecture":"x86_64","SecurityOptions":[]}';;
esac''')
        self.tool('uname', 'echo x86_64')
        output = self.base / 'build'
        result = subprocess.run(['bash', str(ROOT / 'pipeline/buildkit.sh'), 'build', str(output)],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--allow security.insecure', self.calls())
        self.assertIn('--target artifacts', self.calls())
        self.assertIn('type=local,dest=', self.calls())
        self.assertIn('ASSEMBLY_ID=', self.calls())
        self.assertNotIn('docker run', self.calls())
        self.assertNotIn('/lib/modules', self.calls())
        self.assertNotIn('/run/udev', self.calls())
        self.assertNotIn('zpool', self.calls())
        again = subprocess.run(['bash', str(ROOT / 'pipeline/buildkit.sh'), 'build', str(output)],
                               env=self.env, capture_output=True, text=True)
        self.assertNotEqual(again.returncode, 0)
