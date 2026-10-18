#!/usr/bin/env python3
"""Exercise UEFI boot and workspace recovery on disposable copies of a pair."""
import argparse
import base64
import datetime
import getpass
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / 'stages/04-test-pair/scenarios.json'
GUEST = ROOT / 'stages/04-test-pair/guest-checks.sh'


def command(*args):
    # Run a host operation without interpreting shell syntax.
    return subprocess.check_output(args, text=True).strip()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify_inputs(out, pair, sys_block=Path('/sys/block')):
    # Reject incomplete, unexpected, or host-attached source images.
    require(out.is_dir(), 'OUT must be an initialized image directory')
    require((out / 'READY').is_file() and not (out / 'INITIALIZING').exists(),
            'Use a completed initialized pair with READY and no INITIALIZING marker')
    info = (out / 'WORKSPACE-INFO.txt').read_text().splitlines()
    require(f'pair={pair}' in info and 'state=active' in info,
            'This suite requires a freshly initialized active pair matching PAIR')
    names = ('usb-a.img', 'usb-b.img', 'images.json')
    files = [out / name for name in names]
    require(all(path.is_file() and not path.is_symlink() for path in files),
            'Inputs must be regular files, not devices or symlinks')
    require(sys_block.is_dir(), 'Linux sysfs is required for host loop-device checks')
    for entry in sys_block.glob('loop*/loop/backing_file'):
        backing = entry.read_text().strip()
        # sysfs can omit the leading slash for an absolute backing path.
        if backing:
            resolved = Path('/' + backing.lstrip('/')).resolve()
            require(resolved not in files[:2], f'Source image is attached to {entry.parts[-3]}')


def serial_config(config):
    # Enable the test console without changing the image's boot selection.
    lines = config.splitlines(keepends=True)
    count = 0
    for index, line in enumerate(lines):
        if re.match(r'^\s*linux\s+/live/vmlinuz\s', line):
            require('console=' not in line, 'Unexpected console configuration; inspect manually')
            lines[index] = line.rstrip('\n') + ' console=tty0 console=ttyS0,115200n8\n'
            count += 1
    require(count == 1, 'Expected exactly one live kernel entry in the test copy')
    return ''.join(lines)


def prepare_copies(out, work, upgrade_release=None):
    # Copy the raw disks while QEMU's normal source locking is in effect.
    for side in ('a', 'b'):
        source = out / f'usb-{side}.img'
        target = work / source.name
        command('qemu-img', 'convert', '-f', 'raw', '-O', 'raw', str(source), str(target))

        # Apply optional OS upgrades only to the disposable copy.
        if upgrade_release is not None:
            subprocess.run([sys.executable, str(ROOT / 'stages/06-upgrade-os/copy_test.py'),
                            str(target), str(upgrade_release)], check=True)

        # Enable serial login only in the disposable EFI filesystem.
        config = work / f'grub-{side}.cfg'
        command('mcopy', '-i', f'{target}@@1048576', '::/boot/grub/grub.cfg', str(config))
        config.write_text(serial_config(config.read_text()))
        command('mcopy', '-o', '-i', f'{target}@@1048576', str(config), '::/boot/grub/grub.cfg')


def firmware_pair(code, variables):
    # Select a matching non-Secure-Boot OVMF firmware pair.
    require(bool(code) == bool(variables), 'Specify both --code and --vars together')
    if code:
        paths = Path(code).resolve(), Path(variables).resolve()
        require(all(path.is_file() for path in paths), 'OVMF firmware files are missing')
        return paths
    for suffix in ('_4M', ''):
        paths = tuple(Path(f'/usr/share/OVMF/OVMF_{kind}{suffix}.fd') for kind in ('CODE', 'VARS'))
        if all(path.is_file() for path in paths):
            return paths
    raise RuntimeError('Supply matching non-Secure-Boot firmware with --code and --vars')


def qemu_args(work, sides, code, variables, accel):
    # Attach only disposable disks with no network or host filesystem access.
    args = ['-machine', f'q35,accel={accel}', '-cpu', 'host' if accel == 'kvm' else 'max',
            '-m', '4096', '-smp', '2', '-display', 'none', '-monitor', 'none',
            '-serial', 'stdio', '-nic', 'none',
            '-drive', f'if=pflash,format=raw,readonly=on,file={code}',
            '-drive', f'if=pflash,format=raw,file={variables}']
    for side in sides:
        args += ['-drive', f'if=virtio,format=raw,file={work / ("usb-" + side + ".img")}']
    return args


class Guest:
    def __init__(self, child, expect, timeout, expected_revision=None):
        self.child = child
        self.expect = expect
        self.timeout = timeout
        self.expected_revision = expected_revision

    def login(self):
        # Require root autologin on the serial console.
        prompt = r'root@[^\r\n]*# '
        result = self.child.expect([r'login: ', prompt], timeout=self.timeout)
        require(result == 1, 'Expected automatic root console login')
        self.run('test "$(id -un)" = root')
        if self.expected_revision is not None:
            self.run('test "$(cat /usr/local/share/level1vault/source-commit)" = ' + shlex.quote(self.expected_revision))

        # Restore the shared checks after every ephemeral-root boot.
        encoded = base64.b64encode(GUEST.read_bytes()).decode()
        self.run(': > /tmp/level1vault-qemu-check.b64')
        for offset in range(0, len(encoded), 512):
            self.run('printf %s ' + shlex.quote(encoded[offset:offset + 512]) +
                     ' >> /tmp/level1vault-qemu-check.b64')
        self.run('base64 -d /tmp/level1vault-qemu-check.b64 > /tmp/level1vault-qemu-check.sh')

    def run(self, script, passphrase=None):
        # Report command status without confusing echoed input for a result.
        marker = 'L1V_' + uuid.uuid4().hex
        line = script + "; printf '\\n%s:%s\\n' " + marker + ' "$?"'
        log = self.child.logfile_read
        if passphrase is not None:
            self.child.logfile_read = None
        try:
            self.child.sendline(line)
            if passphrase is not None:
                self.child.expect(r'Enter passphrase for [^\r\n]+:', timeout=self.timeout)
                self.child.sendline(passphrase)
            self.child.expect(marker + r':([0-9]+)\r*\n', timeout=self.timeout)
            require(self.child.match.group(1) == '0', 'Guest acceptance check failed; see the scenario log')
        finally:
            # Never write the unlock exchange or an exception's console buffer to logs.
            self.child.logfile_read = log

    def reboot(self):
        # Require a new kernel boot before accepting the next login prompt.
        self.child.sendline('sudo reboot')
        self.child.expect(r'Linux version ', timeout=self.timeout)
        self.login()

    def poweroff(self):
        # Require the guest to shut down normally after export.
        self.child.sendline('sudo poweroff')
        self.child.expect(self.expect.EOF, timeout=self.timeout)
        self.child.close()
        require(self.child.exitstatus == 0, 'QEMU did not exit cleanly')


def run_suite(args, expect, out, results, firmware, passphrase):
    # Keep copied workspace data temporary even when a test is interrupted.
    run_id = uuid.uuid4().hex[:16]
    completed = []
    expected_revision = None
    if args.upgrade_release is not None:
        expected_revision = json.loads((args.upgrade_release / 'live/release.json').read_text())['source_commit']
    report = {'status': 'running', 'pair': args.pair, 'run': run_id,
              'completed': completed}
    try:
        with tempfile.TemporaryDirectory(prefix='disks-', dir=results) as temporary:
            work = Path(temporary)
            prepare_copies(out, work, args.upgrade_release)
            for scenario in json.loads(SCENARIOS.read_text()):
                print(f"QEMU: {scenario['name']} — {scenario['purpose']}", flush=True)
                variables = work / 'vars.fd'
                shutil.copyfile(firmware[1], variables)
                with (results / (scenario['name'] + '.log')).open('w') as log:
                    child = expect.spawn('qemu-system-x86_64',
                                         qemu_args(work, scenario['drives'], firmware[0], variables, args.accel),
                                         encoding='utf-8', codec_errors='replace', timeout=args.timeout)
                    child.logfile_read = log
                    try:
                        guest = Guest(child, expect, args.timeout, expected_revision)
                        guest.login()
                        for phase, *value in scenario['steps']:
                            if phase == 'reboot':
                                guest.reboot()
                            else:
                                argv = ['bash', '/tmp/level1vault-qemu-check.sh', phase,
                                        args.pair, run_id, *value]
                                guest.run(shlex.join(argv), passphrase if phase == 'mount' else None)
                        guest.poweroff()
                    finally:
                        if child.isalive():
                            child.close(force=True)
                completed.append(scenario['name'])
        report['status'] = 'passed'
    finally:
        if report['status'] != 'passed':
            report['status'] = 'failed'
        (results / 'result.json').write_text(json.dumps(report, indent=2) + '\n')


def prepare_results(results, fallback=None):
    # Reserve private test storage before checking images or requesting secrets.
    require(',' not in str(results), 'QEMU paths must not contain commas')
    try:
        results.mkdir(parents=True, mode=0o700)
    except PermissionError:
        if fallback is not None:
            return prepare_results(fallback)
        raise RuntimeError(
            f'Cannot create results directory: {results}. '
            'Run as your desktop user and choose a writable directory with '
            'QEMU_ARGS="--results $HOME/level1vault-qemu-RUN" '
            '(replace RUN with a unique name).') from None
    except FileExistsError:
        raise RuntimeError('Use a new results directory') from None
    return results


def main():
    # Read host test options without accepting secrets in command-line arguments.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='build/default/pairs/1')
    parser.add_argument('--pair', default='1', help='numeric pair ID (default: 1)')
    parser.add_argument('--code')
    parser.add_argument('--vars')
    parser.add_argument('--accel', choices=('kvm', 'tcg'), default='kvm')
    parser.add_argument('--timeout', type=int, default=600, help='seconds per guest operation')
    parser.add_argument('--results', help='new directory for logs and result.json; otherwise choose writable storage automatically')
    parser.add_argument('--upgrade-release', type=Path, help='test an OS release on disposable copies before booting')
    parser.add_argument('--passphrase-stdin', action='store_true', help='read one passphrase line from stdin')
    args = parser.parse_args()
    require(re.fullmatch(r'[1-9][0-9]*', args.pair) is not None, 'PAIR must be a positive integer')
    require(args.timeout > 0, 'Timeout must be positive')

    # Check prerequisites before copying disks or requesting the encryption passphrase.
    try:
        import pexpect
    except ImportError:
        raise RuntimeError('Install python3-pexpect with your distribution package manager') from None
    for tool in ('qemu-system-x86_64', 'qemu-img', 'mcopy'):
        require(shutil.which(tool) is not None, f'Missing host tool: {tool}')
    if args.accel == 'kvm':
        require(os.access('/dev/kvm', os.R_OK | os.W_OK), 'KVM is inaccessible; fix permissions or use --accel tcg')
    out = Path(args.out).resolve()
    firmware = firmware_pair(args.code, args.vars)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run_name = f'{stamp}-{uuid.uuid4().hex[:8]}'
    results = Path(args.results).resolve() if args.results else out.parent.parent / 'validation' / run_name
    fallback = None if args.results else Path.home() / 'level1vault-qemu' / run_name
    require(all(',' not in str(path) for path in firmware), 'QEMU paths must not contain commas')
    results = prepare_results(results, fallback)
    print(f'Results: {results}', flush=True)
    if args.upgrade_release is not None:
        require((args.upgrade_release / 'live/release.json').is_file(), 'OS upgrade release is missing')
        for tool in ('mdir', 'mdeltree'):
            require(shutil.which(tool) is not None, f'Missing host tool: {tool}')
    print('Checking source images; stop all VMs and detach their host loop mappings.', flush=True)
    verify_inputs(out, args.pair)
    if args.passphrase_stdin:
        passphrase = sys.stdin.readline().rstrip('\n')
    else:
        require(sys.stdin.isatty(), 'Use a terminal or explicitly request --passphrase-stdin')
        passphrase = getpass.getpass('Workspace passphrase (not logged): ')
    require(bool(passphrase) and '\r' not in passphrase, 'Passphrase must be a nonempty single line')

    # Protect test logs and run the acceptance suite on private copies.
    os.umask(0o077)
    try:
        run_suite(args, pexpect, out, results, firmware, passphrase)
    except (pexpect.TIMEOUT, pexpect.EOF):
        raise RuntimeError('Guest timed out or exited unexpectedly; inspect the scenario log (unlock exchange omitted)') from None
    print('QEMU acceptance passed; original images were not modified.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'QEMU test failed: {error}')
    except KeyboardInterrupt:
        sys.exit('QEMU test interrupted; disposable disks removed, logs retained.')
