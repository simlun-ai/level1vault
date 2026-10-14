#!/usr/bin/env bash
set -euo pipefail
# Run only inside the disposable CI Trixie container; modifies its account files.
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
"$SOURCE_DIR/live/50-account.hook.chroot"
install -m 0755 "$SOURCE_DIR/live/workspace-mount" /usr/local/sbin/workspace-mount
install -m 0755 "$SOURCE_DIR/live/workspace-umount" /usr/local/sbin/workspace-umount
[[ $(id -u simlun) == 2000 && $(id -g simlun) == 2000 ]]
visudo -cf /etc/sudoers
# Verify the actual stored password using the system's crypt implementation.
python3 - <<'PY'
import ctypes, ctypes.util, subprocess
entry = subprocess.check_output(['getent', 'shadow', 'simlun'], text=True).strip().split(':')[1]
lib = ctypes.CDLL(ctypes.util.find_library('crypt'))
lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
lib.crypt.restype = ctypes.c_char_p
assert lib.crypt(b'password', entry.encode()) == entry.encode()
PY
su - simlun -c 'sudo -n id -u' | grep -qx 0
su - simlun -c 'command -v workspace-mount && command -v workspace-umount'
su - simlun -c 'sudo -n sh -c "command -v workspace-mount && command -v workspace-umount"'
grep -qx LIVE_USERNAME=simlun /etc/live/config.conf.d/level1vault.conf
grep -qx LIVE_CONFIG_NOCOMPONENTS=user-setup,sudo /etc/live/config.conf.d/level1vault.conf
