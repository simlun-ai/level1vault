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
        repository = self.base / 'repository'
        repository.mkdir()
        (repository / 'source.bundle').write_text('fixture')
        (repository / 'commit').write_text('fixture')
        self.log = self.base / 'calls'
        self.env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}', LOG=str(self.log),
                             LEVEL1VAULT_REPOSITORY=str(repository))


    def tool(self, name, body):
        file = self.bin / name
        file.write_text('#!/bin/sh\nset -eu\n' + body + '\n')
        file.chmod(0o755)


    def calls(self):
        return self.log.read_text() if self.log.exists() else ''


    def test_configure_installs_sources_and_refuses_overwrite(self):
        self.tool('lb', 'printf "%s\\n" "$@" >> "$LOG"')
        first = subprocess.run(['bash', str(ROOT / 'stages/01-build-live/configure.sh')], cwd=self.base,
                               env=self.env, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        target = self.base / 'config/includes.chroot/usr/local/sbin/workspace-mount'
        self.assertEqual(target.read_bytes(), (ROOT / 'runtime/workspace-mount').read_bytes())
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        installed = self.base / 'config/includes.chroot/usr/local/lib/level1vault/build/repository'
        self.assertEqual((installed / 'source.bundle').read_text(), 'fixture')
        self.assertTrue((self.base / 'config/hooks/live/60-repository.hook.chroot').is_file())
        for name in ('zshrc', 'tmux.conf', 'starship.toml'):
            staged = self.base / 'config/includes.chroot/usr/local/lib/level1vault/build/user-config' / name
            self.assertEqual(staged.read_bytes(), (ROOT / 'stages/01-build-live/user-config' / name).read_bytes())
        self.assertIn('linux-image', self.calls())
        self.assertNotIn('linux-headers', self.calls())
        self.assertIn('--bootloaders\ngrub-efi\n', self.calls())
        self.assertNotIn('syslinux', self.calls())
        self.assertIn('loglevel=4', self.calls())
        console = self.base / 'config/includes.chroot/etc/sysctl.d/99-level1vault-console.conf'
        self.assertEqual(console.read_text(), 'kernel.printk = 4 4 1 7\n')
        self.assertIn('--memtest\nnone\n', self.calls())
        again = subprocess.run(['bash', str(ROOT / 'stages/01-build-live/configure.sh')], cwd=self.base,
                               env=self.env, capture_output=True, text=True)
        self.assertNotEqual(again.returncode, 0)
