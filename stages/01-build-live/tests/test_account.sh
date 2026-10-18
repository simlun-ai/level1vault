#!/usr/bin/env bash
set -euo pipefail
# Run only inside the disposable CI Trixie container; modifies its account files.
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)

# Install the live account and workspace helpers.
install -d /usr/local/lib/level1vault/build
cp -a "$SOURCE_DIR/stages/01-build-live/user-config" /usr/local/lib/level1vault/build/
"$SOURCE_DIR/stages/01-build-live/hooks/50-account.hook.chroot"
install -m 0755 "$SOURCE_DIR/runtime/workspace-discover" /usr/local/sbin/workspace-discover
install -m 0755 "$SOURCE_DIR/runtime/workspace-mount" /usr/local/sbin/workspace-mount
install -m 0755 "$SOURCE_DIR/runtime/workspace-umount" /usr/local/sbin/workspace-umount

# Install a complete offline repository through the real image hook.
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
git init --quiet -b main "$fixture/source"
git -C "$fixture/source" -c user.name=Fixture -c user.email=fixture@example.invalid \
    commit --quiet --allow-empty -m 'Offline repository fixture'
mkdir -p /usr/local/lib/level1vault/build
python3 "$SOURCE_DIR/stages/01-build-live/repository.py" bundle "$fixture/source" /usr/local/lib/level1vault/build/repository
cp "$SOURCE_DIR/stages/01-build-live/repository.py" /usr/local/lib/level1vault/build/
"$SOURCE_DIR/stages/01-build-live/hooks/60-repository.hook.chroot"
[[ $(stat -c '%u:%g' /home/simlun/level1vault/.git) == 2000:2000 ]]
su - simlun -c 'git -C /home/simlun/level1vault fsck --full'

# Verify account identity and sudo configuration.
[[ $(id -u simlun) == 2000 && $(id -g simlun) == 2000 ]]
[[ $(getent passwd simlun | cut -d: -f7) == /usr/bin/zsh ]]
su - simlun -c '[[ -o login ]]'
visudo -cf /etc/sudoers
# Verify the configured account password.
python3 - <<'PY'
import ctypes, ctypes.util, subprocess
entry = subprocess.check_output(['getent', 'shadow', 'simlun'], text=True).strip().split(':')[1]
lib = ctypes.CDLL(ctypes.util.find_library('crypt'))
lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
lib.crypt.restype = ctypes.c_char_p
assert lib.crypt(b'password', entry.encode()) == entry.encode()
PY

# Verify passwordless sudo and command availability.
su - simlun -c 'sudo -n id -u' | grep -qx 0
su - simlun -c 'command -v workspace-mount && command -v workspace-umount'
su - simlun -c 'sudo -n sh -c "command -v workspace-mount && command -v workspace-umount"'

# Verify the interactive editor alias, real tool commands, and manual-page aliases.
su - simlun -c "zsh -ic 'alias vim && vim --headless -u NONE -i NONE +q'" | grep -qx 'vim=nvim'
su - simlun -c 'fd --version && bat --version'
su - simlun -c "zsh -il '$SOURCE_DIR/stages/01-build-live/tests/test_user_config.zsh'"
for page in fd bat nvim; do
    su - simlun -c "MANPAGER=cat man 1 $page" > "$fixture/manpage"
    test -s "$fixture/manpage"
done

# Verify that live boot preserves the account settings.
grep -qx LIVE_USERNAME=simlun /etc/live/config.conf.d/level1vault.conf
grep -qx LIVE_CONFIG_NOCOMPONENTS=user-setup,sudo /etc/live/config.conf.d/level1vault.conf

# Verify the guide covers every installed workspace command.
for command in workspace-mount workspace-umount workspace-sync workspace-activate; do
    grep -Fq "sudo $command" /etc/motd
done

# Verify the message appears during an actual login.
python3 - <<'PYLOGIN'
import errno, os, pty, select, signal, time
pid, fd = pty.fork()
if pid == 0:
    os.execvp('login', ['login', '-f', 'simlun'])
with open('/etc/motd', 'rb') as motd:
    expected = motd.read()
output = b''
try:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if select.select([fd], [], [], 1)[0]:
            try:
                chunk = os.read(fd, 65536)
            except OSError as e:
                if e.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            output += chunk
            if expected in output.replace(b'\r', b''):
                break
    assert expected in output.replace(b'\r', b''), output.decode(errors='replace')
finally:
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    os.close(fd)
PYLOGIN
