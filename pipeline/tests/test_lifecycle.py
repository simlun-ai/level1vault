"""Check privilege handoff, cleanup exclusion, and published metadata ownership."""
import fcntl
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('lifecycle', ROOT / 'pipeline/lifecycle.py')
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)
sys.path.insert(0, str(ROOT / 'common'))
import artifact_owner


class LifecycleTests(unittest.TestCase):
    def test_root_coordinator_builds_as_original_user_with_explicit_settings(self):
        with patch.dict(os.environ, {'SUDO_UID': '1234', 'PAIR': '3', 'FRESH': '1',
                                     'LEVEL1VAULT_LOCKED': '1'}, clear=True), \
                patch.object(lifecycle.os, 'geteuid', return_value=0):
            command = lifecycle.user_command(['bash', 'pipeline/buildkit.sh', 'build', '/out'])
        self.assertEqual(command[:6], ['sudo', '-H', '-u', '#1234', 'env', 'PAIR=3'])
        self.assertIn('LEVEL1VAULT_LOCKED=1', command)
        self.assertEqual(command[-4:], ['bash', 'pipeline/buildkit.sh', 'build', '/out'])
        self.assertFalse(any('passphrase' in arg for arg in command))

    def test_cleanup_lock_rejects_operation_before_any_command_runs(self):
        with lifecycle.git_path('level1vault.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            result = subprocess.run([sys.executable, str(ROOT / 'pipeline/lifecycle.py'), 'clean',
                                     '/must-not-be-touched'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('another checkout operation', result.stderr)

    def test_cleanup_excludes_new_builds(self):
        with lifecycle.git_path('level1vault.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run([sys.executable, str(ROOT / 'pipeline/lifecycle.py'), 'shared',
                                     sys.executable, '-c', 'print("unexpected execution")'],
                                    capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('unexpected execution', result.stdout)

    def test_metadata_publishing_changes_only_explicit_paths_and_skips_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'report'
            path.write_text('failed operation report')
            with patch.dict(os.environ, {'SUDO_UID': '1234', 'SUDO_GID': '5678'}), \
                    patch.object(artifact_owner.os, 'geteuid', return_value=0), \
                    patch.object(artifact_owner.os, 'fchown') as chown, \
                    patch.object(artifact_owner.os, 'fstat', return_value=type('Info', (), {
                        'st_uid': 0, 'st_mode': 0o100644, 'st_nlink': 1})()):
                artifact_owner.publish(path, Path(tmp) / 'missing')
            self.assertEqual(chown.call_count, 1)
            self.assertEqual(chown.call_args.args[1:], (1234, 5678))

    def test_metadata_publishing_refuses_symlinks_and_hardlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            file = base / 'report'
            file.touch()
            (base / 'link').symlink_to(file)
            (base / 'alias').symlink_to(base, target_is_directory=True)
            os.link(file, base / 'hardlink')
            with patch.dict(os.environ, {'SUDO_UID': '1234', 'SUDO_GID': '5678'}), \
                    patch.object(artifact_owner.os, 'geteuid', return_value=0), \
                    patch.object(artifact_owner.os, 'fchown') as chown:
                for path in (base / 'link', base / 'alias/report', base / 'hardlink'):
                    with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                        artifact_owner.publish(path)
            chown.assert_not_called()
