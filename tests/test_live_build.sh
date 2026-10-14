#!/usr/bin/env bash
set -euo pipefail
# Run in the builder container against a completed build, mounted at /build.
# Extract SquashFS and inspect actual target artifacts; do not load its modules.
[[ $# -eq 1 ]] || { echo "Usage: $0 BUILD_DIRECTORY" >&2; exit 2; }
BUILD_DIR=$(realpath -- "$1")
TASK_TMP=$(mktemp -d)
trap 'rm -rf -- "$TASK_TMP"' EXIT
xorriso -osirrox on -indev "$BUILD_DIR/live-image-amd64.hybrid.iso" \
  -extract /live "$TASK_TMP/live"
unsquashfs -processors 1 -no-progress -d "$TASK_TMP/root" "$TASK_TMP/live/filesystem.squashfs" >/dev/null
ROOT="$TASK_TMP/root"
KERNEL=$(cat "$ROOT/usr/local/share/level1vault/kernel-version")
chroot "$ROOT" modinfo -k "$KERNEL" zfs
VERMAGIC=$(chroot "$ROOT" modinfo -k "$KERNEL" -F vermagic zfs)
[[ $VERMAGIC == "$KERNEL "* ]] || { echo 'ZFS kernel vermagic mismatch' >&2; exit 1; }
test -x "$ROOT/usr/local/sbin/mount-workspace"
test ! -e "$ROOT/etc/hostid"
test ! -e "$ROOT/etc/zfs/zpool.cache"
for unit in zfs-import-cache.service zfs-import-scan.service zfs-mount.service; do
  [[ $(readlink "$ROOT/etc/systemd/system/$unit") == /dev/null ]]
done
[[ $(readlink "$ROOT/etc/systemd/system-generators/zfs-mount-generator") == /dev/null ]]
# Validate the real initramfs was built with the live-boot components.
INITRD="$TASK_TMP/live/initrd.img-$KERNEL"
[[ -f $INITRD ]] || INITRD="$TASK_TMP/live/initrd.img"
lsinitramfs "$INITRD" > "$TASK_TMP/initramfs-files"
grep -q 'scripts/live' "$TASK_TMP/initramfs-files"
test -f "$BUILD_DIR/out/IMAGES-PREPARED"
test ! -e "$BUILD_DIR/out/READY"
(cd "$BUILD_DIR/out" && sha256sum -c PREPARED-SHA256SUMS)
printf 'Full Debian Live build inspected successfully: kernel=%s\n' "$KERNEL"
