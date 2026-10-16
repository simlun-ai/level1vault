"""Verify OS release reuse and build failure behavior without USB or Docker access."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('upgrade_pipeline', ROOT / 'pipeline/upgrade-os.py')
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class UpgradePipelineTests(unittest.TestCase):
    def test_ready_release_is_reused_and_original_disk_identities_are_passed_to_updater(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp)
            (release / 'UPGRADE-READY').touch()
            with patch.object(workflow.upgrade, 'prerequisites'), \
                    patch.object(workflow.upgrade, 'inspect_pair', return_value=['observed identities']), \
                    patch.object(workflow, 'release_directory', return_value=release), \
                    patch.object(workflow, 'open'), patch.object(workflow.fcntl, 'flock'), \
                    patch.object(workflow.subprocess, 'run') as run, \
                    patch.object(workflow.upgrade, 'apply_release') as apply:
                workflow.execute('2', Path('/a'), Path('/b'))
            run.assert_not_called()
            apply.assert_called_once_with(release, '2', Path('/a'), Path('/b'), expected=['observed identities'])

    def test_failed_build_never_calls_usb_updater(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp) / 'new'
            with patch.object(workflow.upgrade, 'prerequisites'), \
                    patch.object(workflow.upgrade, 'inspect_pair'), \
                    patch.object(workflow, 'release_directory', return_value=release), \
                    patch.object(workflow, 'open'), patch.object(workflow.fcntl, 'flock'), \
                    patch.object(workflow.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'build')), \
                    patch.object(workflow.upgrade, 'apply_release') as apply:
                with self.assertRaises(subprocess.CalledProcessError):
                    workflow.execute('2', Path('/a'), Path('/b'))
            apply.assert_not_called()
            self.assertFalse((release / 'UPGRADE-READY').exists())

    def test_dirty_tracked_source_is_rejected_for_new_builds(self):
        with patch.object(workflow, 'git', return_value=' M packages.list'):
            with self.assertRaisesRegex(ValueError, 'Commit tracked changes'):
                workflow.release_directory(None)
        self.assertEqual(workflow.release_directory('/chosen/release'), Path('/chosen/release'))
