# Stage 4: test a pair

Input: an initialized active pair, pair ID, passphrase, and OVMF firmware. Output: private logs and `result.json` under the build run’s `validation/` directory, falling back to `~/level1vault-qemu/<test-run>/` if permissions prevent that. Run `make test-qemu` as your desktop user on the workstation for pair 1; no extra arguments are required (see [QEMU setup](../../docs/QEMU.md)). This optional stage tests disposable copies; originals remain unchanged. On failure inspect the report and logs, then rerun with a new results directory. Tests: `make test` checks the runner and this generated manual; actual boots are workstation-only.

For OS-upgrade testing, `QEMU_ARGS="--upgrade-release /path/to/release"` replaces the OS payload only in disposable copies before these same checks. See [OS upgrade acceptance](../../docs/UPGRADES.md#workstation-qemu-acceptance).

## Shared manual acceptance checks

[QEMU setup and automated runner](../../docs/QEMU.md) · [Documentation index](../../docs/README.md)

This page is generated from `stages/04-test-pair/guest-checks.sh` and `stages/04-test-pair/scenarios.json`.
Run `python3 stages/04-test-pair/generate-manual.py` after editing either source; `make test` rejects drift.

For manual testing, use the QEMU launch command in [QEMU.md](../../docs/QEMU.md).
Keep the same writable image pair throughout the sequence. Stop QEMU before
changing the attached drives, and start each scenario with a fresh copy of OVMF
variables. Run every scenario in order; each consumes the preceding writes.
Use a disposable initialized **active** pair, not a replica or physical disks.

At each automatic root login (including after reboot), save the following script as
`/tmp/level1vault-qemu-check.sh` in the guest. The live root is ephemeral, so the
script and shell variables must be recreated after every boot. Set `PAIR` to
your pair ID and reuse the **same** 16-character hexadecimal `RUN` in every boot:

```bash
PAIR=1
RUN=0123456789abcdef
cat > /tmp/level1vault-qemu-check.sh <<'CHECKS'
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
    sudo workspace-umount "$PAIR"
    [[ -z $(sudo zpool list -H -o name) ]]
    ;;
  *) echo 'Unknown guest check' >&2; exit 2 ;;
esac
printf 'PASS: %s\n' "$PHASE"
CHECKS
```

Enter the workspace passphrase when `mount` prompts. Each check must print `PASS`; stop on any failure.

## both-reboot

Attach **A and B**. Both drives boot, the workspace persists, and the root overlay resets.

```bash
bash /tmp/level1vault-qemu-check.sh boot "$PAIR" "$RUN"
bash /tmp/level1vault-qemu-check.sh mount "$PAIR" "$RUN" ONLINE
bash /tmp/level1vault-qemu-check.sh write "$PAIR" "$RUN" initial
bash /tmp/level1vault-qemu-check.sh export "$PAIR" "$RUN"
sudo reboot
# After login, recreate the script and PAIR/RUN variables above.
bash /tmp/level1vault-qemu-check.sh boot "$PAIR" "$RUN"
bash /tmp/level1vault-qemu-check.sh mount "$PAIR" "$RUN" ONLINE
bash /tmp/level1vault-qemu-check.sh verify "$PAIR" "$RUN" initial
bash /tmp/level1vault-qemu-check.sh export "$PAIR" "$RUN"
sudo poweroff
```

## a-alone

Attach **A**. Drive A boots independently and accepts degraded writes.

```bash
bash /tmp/level1vault-qemu-check.sh boot "$PAIR" "$RUN"
bash /tmp/level1vault-qemu-check.sh mount "$PAIR" "$RUN" DEGRADED
bash /tmp/level1vault-qemu-check.sh verify "$PAIR" "$RUN" initial
bash /tmp/level1vault-qemu-check.sh write "$PAIR" "$RUN" from-a
bash /tmp/level1vault-qemu-check.sh export "$PAIR" "$RUN"
sudo poweroff
```

## rejoin-a

Attach **A and B**. Drive B rejoins and the mirror retains A's degraded write.

```bash
bash /tmp/level1vault-qemu-check.sh boot "$PAIR" "$RUN"
bash /tmp/level1vault-qemu-check.sh mount "$PAIR" "$RUN" rejoining
bash /tmp/level1vault-qemu-check.sh rejoin "$PAIR" "$RUN"
bash /tmp/level1vault-qemu-check.sh verify "$PAIR" "$RUN" from-a
bash /tmp/level1vault-qemu-check.sh export "$PAIR" "$RUN"
sudo poweroff
```

## b-alone

Attach **B**. Drive B boots independently and accepts degraded writes.

```bash
bash /tmp/level1vault-qemu-check.sh boot "$PAIR" "$RUN"
bash /tmp/level1vault-qemu-check.sh mount "$PAIR" "$RUN" DEGRADED
bash /tmp/level1vault-qemu-check.sh verify "$PAIR" "$RUN" from-a
bash /tmp/level1vault-qemu-check.sh write "$PAIR" "$RUN" from-b
bash /tmp/level1vault-qemu-check.sh export "$PAIR" "$RUN"
sudo poweroff
```

## rejoin-b

Attach **A and B**. Drive A rejoins and the mirror retains B's degraded write.

```bash
bash /tmp/level1vault-qemu-check.sh boot "$PAIR" "$RUN"
bash /tmp/level1vault-qemu-check.sh mount "$PAIR" "$RUN" rejoining
bash /tmp/level1vault-qemu-check.sh rejoin "$PAIR" "$RUN"
bash /tmp/level1vault-qemu-check.sh verify "$PAIR" "$RUN" from-b
bash /tmp/level1vault-qemu-check.sh export "$PAIR" "$RUN"
sudo poweroff
```

Physical USB transport, firmware compatibility, Secure Boot settings, and unexpected power loss remain separate hardware checks.
