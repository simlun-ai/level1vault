"""Check QEMU orchestration safety and manual/automated acceptance parity."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('test_qemu_runner', ROOT / 'stages/04-test-pair/run.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class QemuTests(unittest.TestCase):
    def setUp(self):
        # Prepare small initialized artifacts without accessing host ZFS.
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.out = self.base / 'out'
        self.out.mkdir()
        self.sysfs = self.base / 'sys'
        self.sysfs.mkdir()
        (self.out / 'READY').touch()
        (self.out / 'WORKSPACE-INFO.txt').write_text('pair=1\nstate=active\n')
        for name in ('usb-a.img', 'usb-b.img', 'images.json'):
            (self.out / name).write_text(name)
        self.checksums = {name: runner.digest(self.out / name)
                          for name in ('usb-a.img', 'usb-b.img', 'images.json')}
        (self.out / 'SHA256SUMS').write_text(''.join(f'{checksum}  {name}\n'
                                                  for name, checksum in self.checksums.items()))

    def verify(self):
        return runner.verify_inputs(self.out, '1', self.sysfs)

    def test_preflight_checks_identity_checksums_and_partial_setup(self):
        self.assertEqual(self.verify(), self.checksums)
        (self.out / 'INITIALIZING').touch()
        with self.assertRaisesRegex(RuntimeError, 'completed'):
            self.verify()
        (self.out / 'INITIALIZING').unlink()
        (self.out / 'usb-a.img').write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch'):
            self.verify()

    def test_rejects_attached_images_and_symlinks(self):
        entry = self.sysfs / 'loop0/loop/backing_file'
        entry.parent.mkdir(parents=True)
        entry.write_text(str(self.out / 'usb-a.img'))
        with self.assertRaisesRegex(RuntimeError, 'attached'):
            self.verify()
        entry.unlink()
        (self.out / 'usb-a.img').unlink()
        (self.out / 'usb-a.img').symlink_to(self.out / 'usb-b.img')
        with self.assertRaisesRegex(RuntimeError, 'regular files'):
            self.verify()

    def test_rejects_replica_wrong_pair_and_unexpected_checksum_paths(self):
        for info in ('pair=1\nstate=replica\n', 'pair=2\nstate=active\n'):
            (self.out / 'WORKSPACE-INFO.txt').write_text(info)
            with self.assertRaisesRegex(RuntimeError, 'active pair'):
                self.verify()
        (self.out / 'WORKSPACE-INFO.txt').write_text('pair=1\nstate=active\n')
        with (self.out / 'SHA256SUMS').open('a') as stream:
            stream.write('a' * 64 + '  ../../unexpected\n')
        with self.assertRaisesRegex(RuntimeError, 'Unexpected'):
            self.verify()

    def test_results_are_private_and_existing_runs_are_preserved(self):
        results = self.base / 'validation' / 'run'
        runner.prepare_results(results)
        self.assertEqual(results.stat().st_mode & 0o777, 0o700)
        marker = results / 'keep'
        marker.write_text('previous run')
        with self.assertRaisesRegex(RuntimeError, 'new results directory'):
            runner.prepare_results(results)
        self.assertEqual(marker.read_text(), 'previous run')

    def test_default_results_fall_back_to_private_user_storage_on_permission_error(self):
        primary = self.base / 'root-owned-build' / 'validation' / 'run'
        fallback = self.base / 'home' / 'level1vault-qemu' / 'run'
        mkdir = Path.mkdir

        def denied_build(path, *args, **kwargs):
            if path == primary:
                raise PermissionError('root-owned build directory')
            return mkdir(path, *args, **kwargs)

        with patch.object(Path, 'mkdir', autospec=True, side_effect=denied_build):
            selected = runner.prepare_results(primary, fallback)
        self.assertEqual(selected, fallback)
        self.assertEqual(selected.stat().st_mode & 0o777, 0o700)
        self.assertFalse(primary.exists())

    def test_writable_default_results_do_not_use_fallback(self):
        primary = self.base / 'validation' / 'run'
        fallback = self.base / 'home' / 'run'
        self.assertEqual(runner.prepare_results(primary, fallback), primary)
        self.assertFalse(fallback.exists())

    def test_results_permission_failure_precedes_image_checks_and_passphrase(self):
        argv = ['run.py', '--pair', '1', '--accel', 'tcg',
                '--results', str(self.base / 'validation' / 'run')]
        with patch.object(sys, 'argv', argv), \
                patch.dict(sys.modules, {'pexpect': Mock()}), \
                patch.object(runner.shutil, 'which', return_value='/tool'), \
                patch.object(runner, 'firmware_pair', return_value=(Path('/code'), Path('/vars'))), \
                patch.object(Path, 'mkdir', side_effect=PermissionError), \
                patch.object(runner, 'verify_inputs') as verify, \
                patch.object(runner.getpass, 'getpass') as prompt:
            with self.assertRaisesRegex(RuntimeError, 'QEMU_ARGS=.*--results'):
                runner.main()
        verify.assert_not_called()
        prompt.assert_not_called()

    def test_serial_patch_preserves_boot_arguments_and_refuses_ambiguity(self):
        original = 'menuentry test {\n    linux /live/vmlinuz boot=live nopersistence\n}\n'
        modified = runner.serial_config(original)
        self.assertIn('boot=live nopersistence console=tty0 console=ttyS0,115200n8', modified)
        self.assertEqual(modified.replace(' console=tty0 console=ttyS0,115200n8', ''), original)
        for invalid in (modified, original + original, 'no live entry'):
            with self.assertRaises(RuntimeError):
                runner.serial_config(invalid)

    def test_qemu_arguments_never_attach_source_disks_or_network(self):
        work = self.base / 'copies'
        args = runner.qemu_args(work, ['b'], Path('/code.fd'), work / 'vars.fd', 'tcg')
        self.assertIn(f'if=virtio,format=raw,file={work}/usb-b.img', args)
        self.assertFalse(any('usb-a.img' in arg or str(self.out) in arg for arg in args))
        self.assertEqual(args[args.index('-nic') + 1], 'none')
        self.assertEqual(args[args.index('-monitor') + 1], 'none')
        self.assertNotIn('-virtfs', args)

    def test_copy_operations_patch_only_verified_copies(self):
        work = self.base / 'copies'
        work.mkdir()

        def fake_command(*args):
            if args[0] == 'qemu-img':
                Path(args[-1]).write_bytes(Path(args[-2]).read_bytes())
            elif args[-2] == '::/boot/grub/grub.cfg':
                Path(args[-1]).write_text('linux /live/vmlinuz boot=live\n')
            else:
                self.assertIn(str(work), args[args.index('-i') + 1])
            return ''

        with patch.object(runner, 'command', side_effect=fake_command), \
                patch.object(runner.subprocess, 'run') as upgrade_copy:
            runner.prepare_copies(self.out, work, self.checksums, self.base / 'release')
        self.assertEqual(upgrade_copy.call_count, 2)
        for call, side in zip(upgrade_copy.call_args_list, ('a', 'b')):
            args = call.args[0]
            self.assertTrue(args[1].endswith('stages/06-upgrade-os/copy_test.py'))
            self.assertEqual(Path(args[2]), work / f'usb-{side}.img')
            self.assertNotIn(str(self.out / f'usb-{side}.img'), args)
        self.assertEqual(self.verify(), self.checksums)
        self.assertIn('console=ttyS0', (work / 'grub-b.cfg').read_text())

    def test_login_accepts_zsh_and_older_bash_prompts(self):
        for prompt in ('level1vault% ', 'simlun@level1vault:~$ '):
            for autologin in (True, False):
                with self.subTest(prompt=prompt, autologin=autologin):
                    child = Mock()
                    child.expect.return_value = 1 if autologin else 0
                    guest = runner.Guest(child, Mock(), 10)
                    guest.run = Mock()
                    guest.login()
                    patterns = child.expect.call_args_list[0].args[0]
                    self.assertRegex(prompt, patterns[1])
                    if not autologin:
                        self.assertRegex(prompt, child.expect.call_args_list[2].args[0])
                        self.assertEqual([call.args[0] for call in child.sendline.call_args_list],
                                         ['simlun', 'password'])
                    guest.run.assert_any_call('test "$(id -un)" = simlun')

    def test_command_status_accepts_serial_line_endings_and_rejects_failed_checks(self):
        for newline in ('\n', '\r\n', '\r\r\n'):
            for status in (0, 1):
                with self.subTest(newline=repr(newline), status=status):
                    child = Mock()
                    guest = runner.Guest(child, Mock(), 10)

                    def expect_status(pattern, **kwargs):
                        sent = child.sendline.call_args.args[0]
                        marker = re.search(r'L1V_[a-f0-9]{32}', sent).group()
                        # Include echoed input before the actual guest result.
                        transcript = sent + newline + marker + ':' + str(status) + newline
                        child.match = re.search(pattern, transcript)
                        self.assertIsNotNone(child.match, repr(transcript))
                        self.assertEqual(child.match.group(0), marker + ':' + str(status) + newline)

                    child.expect.side_effect = expect_status
                    if status == 0:
                        guest.run('test "$(id -un)" = simlun')
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'Guest acceptance check failed'):
                            guest.run('false')

    def test_unlock_exchange_is_not_logged_even_on_failure(self):
        child = Mock()
        log = child.logfile_read
        guest = runner.Guest(child, Mock(), 10)

        def failed_expect(*args, **kwargs):
            self.assertIsNone(child.logfile_read)
            raise RuntimeError('failed')

        child.expect.side_effect = failed_expect
        with self.assertRaises(RuntimeError):
            guest.run('bash checks mount', 'not-a-real-secret')
        self.assertIs(child.logfile_read, log)
        self.assertNotIn('not-a-real-secret', child.sendline.call_args.args[0])

    def test_manual_checks_are_generated_from_the_actual_suite(self):
        subprocess.run([sys.executable, str(ROOT / 'stages/04-test-pair/generate-manual.py'), '--check'], check=True)
        scenarios = json.loads(runner.SCENARIOS.read_text())
        self.assertEqual([s['drives'] for s in scenarios], [['a', 'b'], ['a'], ['a', 'b'], ['b'], ['a', 'b']])
        for scenario in scenarios:
            self.assertEqual(scenario['steps'][0], ['boot'])
            self.assertEqual(scenario['steps'][-1], ['export'])


if __name__ == '__main__':
    unittest.main()
