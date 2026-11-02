#!/usr/bin/env bash
set -euo pipefail

# Validate the requested guest acceptance check.
PHASE=${1:?Phase required}
PAIR=${2:?Pair ID required}
RUN=${3:?Run ID required}
VALUE=${4:-}
[[ $PAIR =~ ^[1-9][0-9]*$ && $RUN =~ ^[a-f0-9]{16}$ ]] || exit 2
POOL=level1-workspace-$PAIR
DATASET=$POOL/data
CHECK=/mnt/workspace/.level1vault-qemu-$RUN
RESET=/etc/.level1vault-qemu-$RUN

# Identify which test marker is unreadable or differs from the expected write.
verify_markers() {
  local path actual
  for path in "$CHECK" /home/simlun/.level1vault-home-check; do
    if ! actual=$(cat "$path"); then
      printf 'FAIL: %s: cannot read marker %s\n' "$PHASE" "$path" >&2
      return 1
    fi
    if [[ $actual != "$VALUE" ]]; then
      printf 'FAIL: %s: marker %s: expected %q, got %q\n' "$PHASE" "$path" "$VALUE" "$actual" >&2
      return 1
    fi
  done
}

# Exercise real service membership, detached-session refusal, and agent shutdown.
check_session_cleanup() {
  local socket=/run/level1vault-session/tmux.sock unit=level1vault-session.service
  local before directory group pid command
  local -a processes
  python3 /usr/local/sbin/workspace-session prepare
  before=$(systemctl show "$unit" --property=MainPID --value)
  [[ $before =~ ^[1-9][0-9]*$ ]]
  # prepare leaves an unattached session: it must be preserved on export refusal.
  if workspace-umount "$PAIR"; then
    echo 'FAIL: export accepted a detached tmux session' >&2
    return 1
  fi
  mountpoint -q /home/simlun
  python3 /usr/local/sbin/workspace-session prepare
  [[ $(systemctl show "$unit" --property=MainPID --value) == "$before" ]]

  # A double-forked process deliberately ignores TERM and keeps the home busy.
  directory=$(mktemp -d /tmp/level1vault-session-check.XXXXXXXX)
  chown 1000:1000 "$directory"
  cat > "$directory/daemon.py" <<'DAEMON'
import os
import signal
import sys
import time
from pathlib import Path

if os.fork():
    sys.exit(0)
os.setsid()
if os.fork():
    os._exit(0)
with open(os.devnull, "r+b", buffering=0) as null:
    for fd in (0, 1, 2):
        os.dup2(null.fileno(), fd)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
os.chdir("/home/simlun")
Path(sys.argv[1]).write_text(str(os.getpid()))
while True:
    time.sleep(60)
DAEMON
  printf -v command 'gpg --list-keys >/dev/null && gpgconf --launch gpg-agent && python3 %q/daemon.py %q/pid && touch %q/ready' \
    "$directory" "$directory" "$directory"
  tmux -N -S "$socket" respawn-pane -k -t workspace "$command"
  for _ in {1..100}; do
    [[ -f $directory/ready && -s $directory/pid ]] && break
    sleep 0.1
  done
  [[ -f $directory/ready && -s $directory/pid ]]
  for _ in {1..100}; do
    [[ $(tmux -N -S "$socket" display-message -p '#{server_sessions}') == 0 ]] && break
    sleep 0.1
  done
  [[ $(tmux -N -S "$socket" display-message -p '#{server_sessions}') == 0 ]]
  processes=("$(cat "$directory/pid")" "$before")
  # Inspect agents without invoking GPG outside the managed service.
  while IFS= read -r pid; do processes+=("$pid"); done < <(pgrep -u 1000 -x gpg-agent)
  [[ ${#processes[@]} -ge 3 ]]
  group=$(systemctl show "$unit" --property=ControlGroup --value)
  [[ -n $group ]]
  for pid in "${processes[@]}"; do
    grep -Fxq "0::$group" "/proc/$pid/cgroup"
  done

  # The ordinary export path must terminate agents and the TERM-resistant daemon.
  cd /
  workspace-umount "$PAIR"
  for pid in "${processes[@]}"; do
    [[ ! -e /proc/$pid ]]
  done
  rm -rf -- "$directory"
}

case $PHASE in
  boot)
    # Verify the stateless live system and manual-only workspace policy.
    [[ $(id -un) == root && $(id -u) == 0 ]]
    [[ $(id -u simlun) == 1000 && $(id -g simlun) == 1000 ]]
    [[ $(getent passwd simlun | cut -d: -f7) == /usr/bin/zsh ]]
    sudo -iu simlun zsh -lic '[[ ${aliases[vim]} == nvim ]] && command -v workspace-mount'
    [[ $(sudo -n id -u) == 0 ]]
    sudo -n sh -c 'command -v workspace-mount && command -v workspace-umount'
    for command in workspace-mount workspace-umount workspace-sync workspace-activate workspace-prune; do
      grep -Fq "sudo $command" /etc/motd
    done
    grep -qw nopersistence /proc/cmdline
    [[ $(findmnt -n -o FSTYPE /) == overlay ]]
    findmnt -rn -t squashfs -o OPTIONS | grep -Eq '(^|,)ro(,|$)'
    [[ ! -e $RESET ]]
    sudo modprobe zfs
    [[ -z $(sudo zpool list -H -o name) ]]
    ;;
  mount)
    # Unlock the workspace and verify its identity, ownership, and health.
    sudo workspace-mount "$PAIR"
    [[ $(findmnt -n -o SOURCE --mountpoint /mnt/workspace) == "$DATASET" ]]
    [[ $(stat -c '%u:%g %a' /mnt/workspace) == '1000:1000 750' ]]
    [[ $(sudo zfs get -H -o value encryption "$DATASET") == aes-256-gcm ]]
    [[ $(sudo zfs get -H -o value readonly "$DATASET") == off ]]
    if [[ $VALUE != rejoining ]]; then
      [[ $(sudo zpool list -H -o health "$POOL") == "$VALUE" ]]
    fi
    [[ $(findmnt -n -o FSROOT --mountpoint /home/simlun) == /home/simlun ]]
    [[ $(stat -c '%u:%g %a' /home/simlun) == '1000:1000 700' ]]
    ;;
  write)
    # Write workspace data and a marker that must disappear after reboot.
    printf '%s\n' "$VALUE" > "$CHECK"
    # Send data on stdin so sudo's login shell cannot expand marker contents.
    printf '%s\n' "$VALUE" | sudo -iu simlun tee /home/simlun/.level1vault-home-check >/dev/null
    verify_markers
    printf 'ephemeral\n' | sudo tee "$RESET" >/dev/null
    ;;
  verify)
    # Verify that the previous workspace write survived.
    verify_markers
    ;;
  rejoin)
    # Verify mirror recovery and a clean scrub with both members present.
    sudo zpool wait -t resilver "$POOL"
    sudo zpool scrub "$POOL"
    sudo zpool wait -t scrub "$POOL"
    [[ $(sudo zpool list -H -o health "$POOL") == ONLINE ]]
    sudo zpool status "$POOL" | grep -q 'errors: No known data errors'
    ;;
  export)
    # Export the workspace before reboot, shutdown, or drive removal.
    cd /
    check_session_cleanup
    [[ -z $(sudo zpool list -H -o name) ]]
    ;;
  *) echo 'Unknown guest check' >&2; exit 2 ;;
esac
printf 'PASS: %s\n' "$PHASE"
