"""Prerequisite reports include every workflow and aggregate apt package advice."""
import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('check_host', ROOT / 'pipeline/check-host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class HostCheckTests(unittest.TestCase):
    def report(self, missing=(), *, pexpect=True, firmware=True, probe=None, module=True):
        def exists(path):
            return module if str(path) == '/sys/module/zfs' else firmware
        def ready(*args):
            if args[:2] == ('docker', 'info'):
                return True, '{"OSType":"linux","Architecture":"x86_64","SecurityOptions":[]}'
            if args[:2] == ('docker', 'context'):
                return True, 'unix:///var/run/docker.sock'
            return True, ''
        stream = io.StringIO()
        with patch.object(host.shutil, 'which', side_effect=lambda name: None if name in missing else '/bin/' + name), \
                patch.object(host.importlib.util, 'find_spec', return_value=object() if pexpect else None), \
                patch.object(host.Path, 'is_file', exists), patch.object(host.Path, 'is_dir', exists), \
                patch.object(host, 'probe', side_effect=probe or ready), \
                patch.object(host.platform, 'system', return_value='Linux'), \
                patch.object(host.platform, 'machine', return_value='x86_64'), \
                patch.dict(host.os.environ, {}, clear=True), contextlib.redirect_stdout(stream):
            status = host.check()
        return status, stream.getvalue()

    def test_missing_tools_modules_and_firmware_are_all_reported_with_deduplicated_packages(self):
        status, report = self.report(('zpool', 'zfs', 'shellcheck', 'qemu-img', 'mcopy', 'dpkg-deb', 'mkfs.vfat'),
                                     pexpect=False, firmware=False)
        self.assertEqual(status, 1)
        command = next(line for line in report.splitlines() if line.startswith('  sudo apt'))
        self.assertEqual(command.count('zfsutils-linux'), 1)
        for package in ('shellcheck', 'qemu-utils', 'mtools', 'dpkg', 'dosfstools', 'python3-pexpect', 'ovmf'):
            self.assertIn(package, command)
        for group in host.GROUPS:
            self.assertIn(group, report)

    def test_healthy_workstation_passes(self):
        status, report = self.report()
        self.assertEqual(status, 0)
        self.assertNotIn('sudo apt install', report)

    def test_missing_buildx_and_unavailable_docker_produce_different_advice(self):
        def unavailable(*args):
            if args[:2] == ('docker', 'buildx'):
                return False, 'no buildx'
            return False, 'permission denied'
        status, report = self.report(probe=unavailable)
        self.assertEqual(status, 1)
        self.assertIn('docker-buildx', report)
        self.assertIn('Docker readiness', report)

    def test_unloaded_zfs_module_fails_without_suggesting_package_replacement(self):
        status, report = self.report(module=False)
        self.assertEqual(status, 1)
        self.assertIn('sudo modprobe zfs', report)
        self.assertNotIn('sudo apt install', report)
