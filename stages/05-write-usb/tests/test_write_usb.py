"""Verify disk-writing guards and command ordering without opening real devices."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('write_usb', ROOT / 'stages/05-write-usb/write.py')
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)


class WriteUsbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name).resolve()
        (self.out / 'READY').touch()
        (self.out / 'WORKSPACE-INFO.txt').write_text('pair=4\nstate=replica\n')
        checksums = []
        for name in ('usb-a.img', 'usb-b.img', 'images.json'):
            (self.out / name).write_text(name)
            checksums.append(hashlib.sha256(name.encode()).hexdigest() + '  ' + name + '\n')
        (self.out / 'SHA256SUMS').write_text(''.join(checksums))
        self.disks = [dict(name='/dev/sda', device_id=1, type='disk', size=1000, tran='usb', ro=False,
                           children=[dict(name='/dev/sda2', type='part', mountpoints=[None], ro=False)]),
                      dict(name='/dev/sdb', device_id=2, type='disk', size=1000, tran='usb', ro=False)]

    def output(self, *args):
        if args[0] == 'losetup':
            return json.dumps({'loopdevices': []})
        if args[1] == 'list':
            return ''
        raise AssertionError(args)

    def write(self):
        writer.write_pair(self.out, '4', Path('/dev/sda'), Path('/dev/sdb'))

    def test_writes_both_images_only_after_validation_and_repairs_gpt(self):
        with patch.object(writer, 'output', side_effect=self.output), \
                patch.object(writer, 'disk_info', side_effect=self.disks), \
                patch.object(writer.subprocess, 'run') as run:
            self.write()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual([cmd[0] for cmd in commands], ['dd', 'sgdisk', 'dd', 'sgdisk', 'sync', 'partprobe'])
        self.assertIn('if=' + str(self.out / 'usb-a.img'), commands[0])
        self.assertIn('of=/dev/sda', commands[0])
        self.assertIn('if=' + str(self.out / 'usb-b.img'), commands[2])
        self.assertIn('of=/dev/sdb', commands[2])

    def test_rejects_second_disk_before_writing_first(self):
        self.disks[1]['size'] = 1
        with patch.object(writer, 'output', side_effect=self.output), \
                patch.object(writer, 'disk_info', side_effect=self.disks), \
                patch.object(writer.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'too small'):
                self.write()
        run.assert_not_called()

    def test_rejects_same_disk_and_aliases(self):
        with patch.object(writer, 'output', side_effect=self.output), \
                patch.object(writer, 'disk_info', side_effect=[self.disks[0], self.disks[0]]), \
                patch.object(writer.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'different disks'):
                self.write()
        run.assert_not_called()

    def test_rejects_unsafe_destinations(self):
        cases = [('type', 'part'), ('tran', 'sata'), ('ro', True),
                 ('mountpoints', ['/']), ('mountpoints', ['[SWAP]']),
                 ('children', [dict(name='/dev/dm-0', type='crypt')])]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                info = copy.deepcopy(self.disks[0])
                info[key] = value
                with self.assertRaises(ValueError):
                    writer.validate_disk(info, 100, set())
        with self.assertRaisesRegex(ValueError, 'export it first'):
            writer.validate_disk(self.disks[0], 100, {Path('/dev/sda2')})
        with self.assertRaisesRegex(ValueError, 'Not a block device'):
            writer.disk_info(self.out / 'usb-a.img')

    def test_source_integrity_failure_prevents_device_access(self):
        (self.out / 'usb-a.img').write_text('changed')
        with patch.object(writer, 'output') as output, patch.object(writer.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                self.write()
        output.assert_not_called()
        run.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'PAIR'):
            writer.verify_images(self.out, '3')

    def test_attached_source_and_imported_destination_are_refused(self):
        loop = json.dumps({'loopdevices': [{'back-file': str(self.out / 'usb-a.img')}]})
        with patch.object(writer, 'output', return_value=loop), patch.object(writer.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'loop mappings'):
                self.write()
        run.assert_not_called()
        with patch.object(writer, 'output', side_effect=[json.dumps({'loopdevices': []}), 'existing-pool',
                                                        '  /dev/sda2 ONLINE 0 0 0']), \
                patch.object(writer, 'disk_info', side_effect=self.disks), \
                patch.object(writer.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'export it first'):
                self.write()
        run.assert_not_called()

    def test_write_failure_stops_before_second_disk(self):
        with patch.object(writer, 'output', side_effect=self.output), \
                patch.object(writer, 'disk_info', side_effect=self.disks), \
                patch.object(writer.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'dd')) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                self.write()
        self.assertEqual(run.call_count, 1)

    def test_make_requires_both_arguments_before_sudo(self):
        for disks in ([], ['DISK_A=/dev/sda'], ['DISK_B=/dev/sdb']):
            result = subprocess.run(['make', 'write-usb', 'SUDO=false', 'DISK_A=', 'DISK_B=', *disks],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Set both DISK_A and DISK_B', result.stderr)
            self.assertNotIn('python3 stages/05-write-usb/write.py', result.stdout)
