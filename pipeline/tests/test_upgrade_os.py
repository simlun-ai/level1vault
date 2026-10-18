"""Verify that applying a release never invokes a build or accepts partial outputs."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('upgrade_pipeline', ROOT / 'pipeline/upgrade-os.py')
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class UpgradePipelineTests(unittest.TestCase):
    def test_ready_release_is_applied_without_building(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp)
            (release / 'UPGRADE-READY').touch()
            with patch.object(workflow.upgrade, 'prerequisites'), \
                    patch.object(workflow.upgrade, 'load_release') as load, \
                    patch.object(workflow.upgrade, 'inspect_pair', return_value=['observed identities']), \
                    patch.object(workflow, 'open'), patch.object(workflow.fcntl, 'flock'), \
                    patch.object(workflow.subprocess, 'run') as run, \
                    patch.object(workflow.upgrade, 'apply_release') as apply:
                workflow.execute('2', Path('/a'), Path('/b'), str(release))
            run.assert_not_called()
            load.assert_called_once_with(release)
            apply.assert_called_once_with(release, '2', Path('/a'), Path('/b'), expected=['observed identities'])

    def test_missing_or_partial_release_never_accesses_disks_or_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            for release in ('', str(Path(tmp) / 'missing'), tmp):
                with self.subTest(release=release), \
                        patch.object(workflow.upgrade, 'inspect_pair') as inspect, \
                        patch.object(workflow.subprocess, 'run') as run, \
                        patch.object(workflow.upgrade, 'apply_release') as apply:
                    with self.assertRaises(ValueError):
                        workflow.execute('2', Path('/a'), Path('/b'), release)
                    inspect.assert_not_called()
                    run.assert_not_called()
                    apply.assert_not_called()

    def test_invalid_release_never_inspects_disks(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'UPGRADE-READY').touch()
            with patch.object(workflow.upgrade, 'load_release', side_effect=ValueError('Invalid payload')), \
                    patch.object(workflow.upgrade, 'inspect_pair') as inspect:
                with self.assertRaisesRegex(ValueError, 'Invalid payload'):
                    workflow.execute('2', Path('/a'), Path('/b'), tmp)
                inspect.assert_not_called()
