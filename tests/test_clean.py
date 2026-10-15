import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('clean', Path(__file__).resolve().parents[1]/'scripts/clean.py')
cleaner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleaner)


class CleanupTests(unittest.TestCase):
    def test_retains_artifacts_and_initialized_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp).resolve()
            (build/'builder-image.json').write_text('[]')
            for name in cleaner.TRANSIENT:
                (build/name).mkdir()
                (build/name/'work').write_text('generated')
            for name in ['out', 'pair-2']:
                (build/name).mkdir()
                (build/name/'usb-a.img').write_text('valuable workspace')
                (build/name/'READY').touch()
            (build/'live-image-amd64.hybrid.iso').write_text('iso')
            (build/'build.log').write_text('diagnostics')
            with patch.object(cleaner, 'output', side_effect=['', json.dumps({'loopdevices': []})]), \
                    patch.object(cleaner, 'mountinfo', return_value=''):
                cleaner.clean(build)
            self.assertFalse((build/'chroot').exists())
            self.assertEqual((build/'out/usb-a.img').read_text(), 'valuable workspace')
            self.assertTrue((build/'pair-2/READY').exists())
            self.assertTrue((build/'build.log').exists())
            self.assertTrue((build/'live-image-amd64.hybrid.iso').exists())

    def test_refuses_mount_container_and_loop(self):
        build = Path('/build-test')
        cases = [
            ('1 2 0:1 / /build-test/chroot/dev rw - tmpfs tmpfs rw', [], {'loopdevices': []}),
            ('', [{'Id': 'running', 'Mounts': [{'Source': '/build-test'}]}], {'loopdevices': []}),
            ('', [{'Mounts': [{'Source': '/'}]}], {'loopdevices': []}),
            ('', [], {'loopdevices': [{'name': '/dev/loop0', 'back-file': '/build-test/out/usb-a.img'}]}),
        ]
        for state in cases:
            with self.subTest(state=state), self.assertRaises(ValueError):
                cleaner.check_idle(build, *state)
        cleaner.check_idle(build, '', [], {'loopdevices': [{'name': '/dev/loop1', 'back-file': '/unrelated/file'}]})

    def test_refuses_unknown_path_symlink_and_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp).resolve()
            with self.assertRaises(ValueError):
                cleaner.clean(build)
            (build/'builder-image.json').write_text('[]')
            (build/'chroot').symlink_to(build, target_is_directory=True)
            with self.assertRaises(ValueError):
                cleaner.clean(build)
        with self.assertRaises(ValueError):
            cleaner.clean(cleaner.ROOT)

    def test_failed_host_inspection_preserves_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp).resolve()
            (build/'builder-image.json').write_text('[]')
            (build/'chroot').mkdir()
            with patch.object(cleaner, 'output', side_effect=OSError('Docker unavailable')):
                with self.assertRaises(OSError):
                    cleaner.clean(build)
            self.assertTrue((build/'chroot').is_dir())
