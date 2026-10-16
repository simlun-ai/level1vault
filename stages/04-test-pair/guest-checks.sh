#!/usr/bin/env bash
set -euo pipefail

# Validate the requested guest acceptance check.
PHASE=${1:?Phase required}
PAIR=${2:?Pair ID required}
RUN=${3:?Run ID required}
VALUE=${4:-}
[[ $PAIR =~ ^[1-9][0-9]*$ && $RUN =~ ^[a-f0-9]{16}$ ]] || exit 2
POOL=workspace-$PAIR
DATASET=$POOL/data
CHECK=/mnt/workspace/.level1vault-qemu-$RUN
RESET=/etc/.level1vault-qemu-$RUN

case $PHASE in
  boot)
    # Verify the stateless live system and manual-only workspace policy.
    [[ $(id -un) == simlun && $(id -u) == 2000 && $(id -g) == 2000 ]]
    [[ $(sudo -n id -u) == 0 ]]
    sudo -n sh -c 'command -v workspace-mount && command -v workspace-umount'
    for command in workspace-mount workspace-umount workspace-sync workspace-activate; do
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
    [[ $(stat -c '%u:%g %a' /mnt/workspace) == '2000:2000 750' ]]
    [[ $(sudo zfs get -H -o value encryption "$DATASET") == aes-256-gcm ]]
    [[ $(sudo zfs get -H -o value readonly "$DATASET") == off ]]
    if [[ $VALUE != rejoining ]]; then
      [[ $(sudo zpool list -H -o health "$POOL") == "$VALUE" ]]
    fi
    ;;
  write)
    # Write workspace data and a marker that must disappear after reboot.
    printf '%s\n' "$VALUE" > "$CHECK"
    printf 'ephemeral\n' | sudo tee "$RESET" >/dev/null
    ;;
  verify)
    # Verify that the previous workspace write survived.
    [[ $(cat "$CHECK") == "$VALUE" ]]
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
    sudo workspace-umount "$PAIR"
    [[ -z $(sudo zpool list -H -o name) ]]
    ;;
  *) echo 'Unknown guest check' >&2; exit 2 ;;
esac
printf 'PASS: %s\n' "$PHASE"
