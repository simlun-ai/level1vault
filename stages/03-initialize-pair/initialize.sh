#!/usr/bin/env bash
set -euo pipefail
# HOST ONLY: use the workstation's own ZFS userland and running kernel module.
[[ $EUID -eq 0 ]] || { echo 'Run with sudo on the host' >&2; exit 1; }
[[ $# -ge 2 && $# -le 3 ]] || { echo "Usage: $0 PREPARED_OUTPUT_DIRECTORY PAIR_ID [--active]" >&2; exit 2; }
PAIR=$2
[[ $PAIR =~ ^[1-9][0-9]*$ ]] || { echo 'PAIR_ID must be a positive integer without leading zeros' >&2; exit 2; }
POOL=workspace-$PAIR
ACTIVE=${3:-}
[[ -z $ACTIVE || $ACTIVE == --active ]] || { echo 'Expected --active or no flag' >&2; exit 2; }
PAIR_STATE=replica
[[ $ACTIVE != --active ]] || PAIR_STATE=active

# Check host tools and prepared image markers.
for cmd in zpool zfs modprobe losetup udevadm blkid wipefs python3 flock realpath mktemp chown chmod; do
  command -v "$cmd" >/dev/null || { echo "Missing host command: $cmd" >&2; exit 1; }
done
OUT_DIR=$(realpath -- "$1")
[[ -f $OUT_DIR/IMAGES-PREPARED && ! -e $OUT_DIR/READY && ! -e $OUT_DIR/INITIALIZING ]] || {
  echo 'Expected fresh prepared images (no READY or INITIALIZING marker)' >&2; exit 1;
}

# Reserve initialization and validate the prepared metadata.
exec 9>/run/lock/level1vault-initialize.lock
flock -n 9 || { echo 'Another initialization is in progress' >&2; exit 1; }
# Recheck after locking in case another invocation finished while we started.
[[ ! -e $OUT_DIR/READY && ! -e $OUT_DIR/INITIALIZING ]] || exit 1
python3 - "$OUT_DIR/images.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
assert m['format'] == 1
assert m['images']['a']['file'] == 'usb-a.img'
assert m['images']['b']['file'] == 'usb-b.img'
assert m['images']['a']['zfs_partuuid'] != m['images']['b']['zfs_partuuid']
PY

# Check host ZFS access and reject images already in use.
modprobe zfs
# This exercises the installed userland against the loaded host module. Ubuntu's
# module and package version strings need not exactly equal Debian's (or each other).
zpool list -H >/dev/null
if zpool list -H -o name "$POOL" >/dev/null 2>&1; then
  echo "Host already has $POOL imported; refusing to touch it" >&2; exit 1
fi
for img in "$OUT_DIR/usb-a.img" "$OUT_DIR/usb-b.img"; do
  [[ -f $img && ! -L $img ]] || { echo "Invalid image: $img" >&2; exit 1; }
  [[ -z $(losetup -j "$img") ]] || { echo "Image already attached: $img" >&2; exit 1; }
done

# Preserve attached devices if initialization cannot finish safely.
A='' B='' POOL_CREATED=0 MOUNT_ROOT=''
cleanup() {
  local rc=$? safe=1
  trap - EXIT
  set +e
  if [[ $POOL_CREATED == 1 ]]; then
    zpool export "$POOL" || { echo 'ERROR: export failed; retaining loop devices' >&2; safe=0; }
  elif [[ -n $A || -n $B ]] && zpool list -H -o name "$POOL" >/dev/null 2>&1; then
    # Covers interruption immediately after create, before POOL_CREATED was set.
    echo 'Pool exists with uncertain creation state; retaining loops for inspection' >&2
    safe=0
  fi
  if [[ $safe == 1 ]]; then
    if [[ -n $A ]]; then losetup -d "$A" || safe=0; fi
    if [[ -n $B ]]; then losetup -d "$B" || safe=0; fi
  fi
  if [[ $safe == 0 ]]; then
    echo "Inspect workspace before detaching A=$A B=$B" >&2
    rc=1
  fi
  if [[ $safe == 1 && -n ${MOUNT_ROOT:-} ]]; then
    rmdir "$MOUNT_ROOT/mnt/workspace" "$MOUNT_ROOT/mnt" "$MOUNT_ROOT" 2>/dev/null || true
  fi
  if [[ $rc != 0 ]]; then
    rm -f "$OUT_DIR/READY"
    echo 'Initialization incomplete. Inspect INITIALIZING and any remaining pool/loops before recovery.' >&2
  fi
  python3 "$(dirname -- "${BASH_SOURCE[0]}")/../../common/artifact_owner.py" \
    "$OUT_DIR/INITIALIZING" "$OUT_DIR/WORKSPACE-INFO.txt" "$OUT_DIR/READY" || rc=1
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Attach and validate the empty workspace partitions.
# Retained on failure: do not rerun over a partially initialized mirror.
touch "$OUT_DIR/INITIALIZING"
A=$(losetup --find --show --partscan "$OUT_DIR/usb-a.img")
B=$(losetup --find --show --partscan "$OUT_DIR/usb-b.img")
udevadm settle --timeout=30
for name in a b; do
  if [[ $name == a ]]; then dev=$A; else dev=$B; fi
  [[ -b ${dev}p2 ]] || { echo "Missing partition: ${dev}p2" >&2; exit 1; }
  expected=$(python3 - "$OUT_DIR/images.json" "$name" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))['images'][sys.argv[2]]['zfs_partuuid'])
PY
)
  [[ $(blkid -s PARTUUID -o value "${dev}p2") == "$expected" ]] || {
    echo 'Partition identity mismatch' >&2; exit 1;
  }
  [[ -z $(wipefs --noheadings --output TYPE "${dev}p2") ]] || {
    echo 'Workspace partition already has a filesystem signature; refusing' >&2; exit 1;
  }
done
# A conservative shared on-disk format, supported by Ubuntu 22.04 OpenZFS 2.1
# and Debian 13 OpenZFS 2.3. Do not enable all of the creator's available features.
MOUNT_ROOT=$(mktemp -d)
zpool create -d -R "$MOUNT_ROOT" -o feature@lz4_compress=enabled \
  -o feature@encryption=enabled \
  -o ashift=12 -o cachefile=none -O mountpoint=none -O canmount=off \
  -O org.level1vault:pair="$PAIR" -O org.level1vault:state="$PAIR_STATE" "$POOL" mirror "${A}p2" "${B}p2"
POOL_CREATED=1

# Create the encrypted workspace for an active pair.
if [[ $ACTIVE == --active ]]; then
  # ZFS reads stdin itself (hidden prompt/confirmation on a terminal). No key
  # file, shell variable, argument or log ever contains the passphrase.
  zfs create -u -o encryption=aes-256-gcm -o keyformat=passphrase -o keylocation=prompt \
    -o mountpoint=/mnt/workspace -o canmount=noauto -o compression=lz4 \
    -o atime=off -o org.openzfs.systemd:ignore=on "$POOL/data"

  # Set workspace ownership and permissions.
  # Mount beneath a private altroot, never over the host's /mnt/workspace.
  zfs mount "$POOL/data"
  chown 1000:1000 "$MOUNT_ROOT/mnt/workspace"
  chmod 0750 "$MOUNT_ROOT/mnt/workspace"
  zfs unmount "$POOL/data"
else
  echo 'Empty receive target: data will be created by workspace-sync.'
fi

# Record the initialized pool and its feature policy.
{
  date -u +initialized_at=%FT%TZ
  zpool --version
  printf 'pool_guid=%s\n' "$(zpool get -H -o value guid "$POOL")"
  printf 'pair=%s\npool=%s\nstate=%s\n' "$PAIR" "$POOL" "$PAIR_STATE"
  printf 'enabled_feature_policy=lz4_compress,encryption_and_dependencies\n'
  zpool status -gP "$POOL"
  zpool get all "$POOL"
} > "$OUT_DIR/WORKSPACE-INFO.txt"

# Export the pool and release the image devices.
zpool export "$POOL"
POOL_CREATED=0
losetup -d "$A"
A=''
losetup -d "$B"
B=''
udevadm settle --timeout=30

# Publish readiness after exporting the pool and detaching both images.
rm -- "$OUT_DIR/INITIALIZING"
touch "$OUT_DIR/READY"
printf 'Mirror initialized and exported. %s is ready for host-side VM tests and USB writing.\n' "$OUT_DIR"
