"""Verify upgrade scope, failures and retries without opening physical disks."""
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from upgrade_fixtures import make_boot, make_release
spec = importlib.util.spec_from_file_location('os_upgrade', ROOT / 'stages/06-upgrade-os/upgrade.py')
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)

LABEL = """LABEL 0
    name: 'level1-workspace-2'
    pool_guid: 200
    guid: 21
    vdev_tree:
        type: 'mirror'
        guid: 999
LABEL 1
    name: 'level1-workspace-2'
    pool_guid: 200
    guid: 21
    vdev_tree:
        type: 'mirror'
        guid: 999
"""


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.release = self.base / 'release'
        self.manifest = make_release(self.release)
        self.esp = self.base / 'esp'
        self.esp.mkdir()
        make_boot(self.esp)

    def test_replace_changes_only_live_and_disk_files_and_supports_retry(self):
        boot = upgrade.boot_identity(self.esp, 'AAAA-BBBB')
        sentinel = self.esp / 'unrelated.txt'
        sentinel.write_text('keep')
        with patch.object(upgrade, 'command'):
            upgrade.replace_payload(self.esp, self.release, self.manifest)
            upgrade.verify_payload(self.esp, self.manifest)
            upgrade.replace_payload(self.esp, self.release, self.manifest)
        self.assertEqual(upgrade.boot_identity(self.esp, 'AAAA-BBBB'), boot)
        self.assertEqual(sentinel.read_text(), 'keep')
        self.assertFalse((self.esp / '.disk/live-uuid-old').exists())
        self.assertTrue((self.esp / '.disk/live-uuid-new').exists())

    def test_interrupted_copy_is_repairable_without_a_second_os_copy(self):
        original_copy = upgrade.shutil.copyfile
        count = 0

        def fail_copy(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('injected interruption')
            return original_copy(*args)

        with patch.object(upgrade.shutil, 'copyfile', side_effect=fail_copy):
            with self.assertRaisesRegex(OSError, 'interruption'):
                upgrade.replace_payload(self.esp, self.release, self.manifest)
        self.assertFalse((self.esp / 'live/release.json').exists())
        with patch.object(upgrade, 'command'):
            upgrade.replace_payload(self.esp, self.release, self.manifest)
        upgrade.verify_payload(self.esp, self.manifest)
        self.assertEqual((self.esp / 'EFI/BOOT/BOOTX64.EFI').read_text(), 'preserved loader')

    def test_invalid_payloads_are_rejected_before_mounting(self):
        self.assertEqual(upgrade.load_release(self.release), self.manifest)
        (self.release / 'live/vmlinuz').write_text('corrupt')
        # Source hashes are checked only against the written USB payload.
        self.assertEqual(upgrade.load_release(self.release), self.manifest)
        with patch.object(upgrade, 'command'):
            upgrade.replace_payload(self.esp, self.release, self.manifest)
        with self.assertRaisesRegex(ValueError, 'OS readback mismatch'):
            upgrade.verify_payload(self.esp, self.manifest)
        self.manifest['files']['../../outside'] = '0' * 64
        import hashlib
        identity = dict(self.manifest)
        identity.pop('release_id')
        self.manifest['release_id'] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        (self.release / 'live/release.json').write_text(json.dumps(self.manifest))
        # Restore the normal file so validation reaches the malicious path.
        (self.release / 'live/vmlinuz').write_text('new OS:live/vmlinuz')
        with self.assertRaisesRegex(ValueError, 'Unsafe payload path'):
            upgrade.load_release(self.release)

    def test_labels_require_consistent_pool_and_distinct_leaf_identity(self):
        self.assertEqual(upgrade.label_identity(LABEL), ('level1-workspace-2', '200', '21'))
        for text in ('missing', LABEL.replace("type: 'mirror'", "type: 'disk'"),
                     LABEL.replace('LABEL 1\n    name:', "LABEL 1\n    name:").replace('pool_guid: 200', 'pool_guid: 201', 1)):
            with self.assertRaises(ValueError):
                upgrade.label_identity(text)
        base = dict(device_id=1, pool='level1-workspace-2', pool_guid='200', leaf_guid='21', esp_uuid='A',
                    table={'partitions': [{'uuid': 'a1'}, {'uuid': 'a2'}]})
        other = dict(base, device_id=2, leaf_guid='22', esp_uuid='B',
                     table={'partitions': [{'uuid': 'b1'}, {'uuid': 'b2'}]})
        with patch.object(upgrade, 'imported_devices', return_value=set()):
            with patch.object(upgrade, 'inspect_disk', side_effect=[base, other]):
                self.assertEqual(len(upgrade.inspect_pair('2', Path('/a'), Path('/b'))), 2)
            for change in ({'pool': 'level1-workspace-3'}, {'pool_guid': 'foreign'}, {'device_id': 1}, {'leaf_guid': '21'}):
                with patch.object(upgrade, 'inspect_disk', side_effect=[base, dict(other, **change)]):
                    with self.assertRaises(ValueError):
                        upgrade.inspect_pair('2', Path('/a'), Path('/b'))

    def test_space_counts_reclaimed_payload_and_rejects_symlinks(self):
        usage = Mock(f_frsize=4096, f_bavail=0)
        with patch.object(upgrade.os, 'statvfs', return_value=usage):
            with self.assertRaisesRegex(ValueError, 'does not fit'):
                upgrade.check_space(self.esp, self.release)
        (self.esp / 'live').rename(self.esp / 'old-live')
        (self.esp / 'live').symlink_to(self.esp / 'old-live', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Unexpected OS payload path'):
            upgrade.check_space(self.esp, self.release)

    def test_failed_unmount_preserves_the_mountpoint(self):
        root = self.base / 'mountpoint'
        root.mkdir()
        with patch.object(upgrade.tempfile, 'mkdtemp', return_value=str(root)), \
                patch.object(upgrade, 'command', side_effect=[None, subprocess.CalledProcessError(1, 'umount')]):
            with self.assertRaises(subprocess.CalledProcessError):
                with upgrade.mounted('/dev/example1', readonly=False):
                    pass
        self.assertTrue(root.is_dir())

    def test_update_flushes_after_unmount_before_final_readback(self):
        events = []
        @contextmanager
        def mounted(esp, readonly):
            events.append('ro' if readonly else 'rw')
            yield self.esp
            events.append('unmount')
        boot = upgrade.boot_identity(self.esp, 'AAAA-BBBB')
        def command(*args):
            events.append(args[0])
        with patch.object(upgrade, 'mounted', side_effect=mounted), \
                patch.object(upgrade, 'command', side_effect=command):
            upgrade.update_esp('/dev/example1', 'AAAA-BBBB', self.release, self.manifest, boot)
        self.assertEqual(events, ['rw', 'sync', 'unmount', 'blockdev', 'ro', 'unmount'])
        upgrade.verify_payload(self.esp, self.manifest)

    def test_pair_preflight_failure_never_starts_writing(self):
        with patch.object(upgrade, 'inspect_pair', side_effect=ValueError('wrong pair')), \
                patch.object(upgrade, 'mounted') as mounted:
            with self.assertRaisesRegex(ValueError, 'wrong pair'):
                upgrade.apply_release(self.release, '2', Path('/a'), Path('/b'))
        mounted.assert_not_called()

    def test_second_disk_is_validated_before_first_update_and_failures_are_reported(self):
        disks = [dict(disk='/a', esp='/a1', esp_uuid='AAAA-BBBB', model='a', serial='1'),
                 dict(disk='/b', esp='/b1', esp_uuid='AAAA-BBBB', model='b', serial='2')]
        mounts = []

        @contextmanager
        def mounted(esp, readonly):
            mounts.append(esp)
            yield self.esp

        def fail_update(*args):
            self.assertEqual(mounts, ['/a1', '/b1'])
            raise OSError('write failed')

        with patch.object(upgrade, 'inspect_pair', return_value=disks), \
                patch.object(upgrade, 'inspect_disk', return_value=disks[0]), \
                patch.object(upgrade, 'imported_devices', return_value=set()), \
                patch.object(upgrade, 'command'), patch.object(upgrade, 'mounted', side_effect=mounted), \
                patch.object(upgrade, 'update_esp', side_effect=fail_update) as update:
            with self.assertRaisesRegex(OSError, 'write failed'):
                upgrade.apply_release(self.release, '2', Path('/a'), Path('/b'))
        self.assertEqual(update.call_count, 1)
        report = json.loads(next((self.release / 'reports').glob('*.json')).read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['completed'], [])

    def test_make_requires_explicit_pair_and_disks_before_sudo(self):
        env = dict(os.environ)
        env.pop('PAIR', None)
        for args in ([], ['PAIR=2'], ['PAIR=2', 'DISK_A=/dev/a'], ['DISK_A=/dev/a', 'DISK_B=/dev/b']):
            result = subprocess.run(['make', 'upgrade-os', 'UPGRADE_DIR=/prepared', 'SUDO=false', 'DISK_A=', 'DISK_B=', *args],
                                    cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('python3 pipeline/upgrade-os.py', result.stdout)
