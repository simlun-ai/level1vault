#!/usr/bin/env bash
set -euo pipefail
# HOST ONLY: use the workstation's own ZFS userland and running kernel module.
[[ $EUID -eq 0 ]] || { echo 'Run with sudo on the host' >&2; exit 1; }
[[ $# -eq 1 ]] || { echo "Usage: $0 PREPARED_OUTPUT_DIRECTORY" >&2; exit 2; }
for cmd in zpool zfs modprobe losetup udevadm blkid wipefs sha256sum python3 flock realpath; do
  command -v "$cmd" >/dev/null || { echo "Missing host command: $cmd" >&2; exit 1; }
done
OUT_DIR=$(realpath -- "$1")
[[ -f $OUT_DIR/IMAGES-PREPARED && ! -e $OUT_DIR/READY && ! -e $OUT_DIR/INITIALIZING ]] || {
  echo 'Expected fresh prepared images (no READY or INITIALIZING marker)' >&2; exit 1;
}
exec 9>/run/lock/level1vault-initialize.lock
flock -n 9 || { echo 'Another initialization is in progress' >&2; exit 1; }
# Recheck after locking in case another invocation finished while we started.
[[ ! -e $OUT_DIR/READY && ! -e $OUT_DIR/INITIALIZING ]] || exit 1
(cd "$OUT_DIR" && sha256sum -c PREPARED-SHA256SUMS)
python3 - "$OUT_DIR/images.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
assert m['format'] == 1
assert m['images']['a']['file'] == 'usb-a.img'
assert m['images']['b']['file'] == 'usb-b.img'
assert m['images']['a']['zfs_partuuid'] != m['images']['b']['zfs_partuuid']
PY
modprobe zfs
# This exercises the installed userland against the loaded host module. Ubuntu's
# module and package version strings need not exactly equal Debian's (or each other).
zpool list -H >/dev/null
if zpool list -H -o name workspace >/dev/null 2>&1; then
  echo 'Host already has workspace imported; refusing to touch it' >&2; exit 1
fi
for img in "$OUT_DIR/usb-a.img" "$OUT_DIR/usb-b.img"; do
  [[ -f $img && ! -L $img ]] || { echo "Invalid image: $img" >&2; exit 1; }
  [[ -z $(losetup -j "$img") ]] || { echo "Image already attached: $img" >&2; exit 1; }
done
A='' B='' POOL_CREATED=0
cleanup() {
  local rc=$? safe=1
  trap - EXIT
  set +e
  if [[ $POOL_CREATED == 1 ]]; then
    zpool export workspace || { echo 'ERROR: export failed; retaining loop devices' >&2; safe=0; }
  elif [[ -n $A || -n $B ]] && zpool list -H -o name workspace >/dev/null 2>&1; then
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
  if [[ $rc != 0 ]]; then
    rm -f "$OUT_DIR/READY"
    echo 'Initialization incomplete. Inspect INITIALIZING and any remaining pool/loops before recovery.' >&2
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
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
zpool create -d -o feature@lz4_compress=enabled \
  -o ashift=12 -o cachefile=none -O mountpoint=none -O canmount=off \
  workspace mirror "${A}p2" "${B}p2"
POOL_CREATED=1
zfs create -u -o mountpoint=/mnt/workspace -o canmount=noauto \
  -o compression=lz4 -o atime=off -o org.openzfs.systemd:ignore=on workspace/data
{
  date -u +initialized_at=%FT%TZ
  zpool --version
  printf 'pool_guid=%s\n' "$(zpool get -H -o value guid workspace)"
  printf 'enabled_feature_policy=lz4_compress_only\n'
  zpool status -gP workspace
  zpool get all workspace
} > "$OUT_DIR/WORKSPACE-INFO.txt"
zpool export workspace
POOL_CREATED=0
losetup -d "$A"
A=''
losetup -d "$B"
B=''
udevadm settle --timeout=30
(cd "$OUT_DIR" && sha256sum usb-a.img usb-b.img images.json > SHA256SUMS)
rm -- "$OUT_DIR/INITIALIZING"
touch "$OUT_DIR/READY"
printf 'Mirror initialized and exported. %s is ready for host-side VM tests and USB writing.\n' "$OUT_DIR"
