#!/usr/bin/env bash
set -euo pipefail

# Validate the requested build inspection scope.
[[ $# -ge 1 && $# -le 2 && ${2:---iso-only} == --iso-only ]] || { echo "Usage: $0 BUILD_DIRECTORY [--iso-only]" >&2; exit 2; }
BUILD_DIR=$(realpath -- "$1")
TASK_TMP=$(mktemp -d)
trap 'rm -rf -- "$TASK_TMP"' EXIT

# Extract the built live system for inspection.
xorriso -osirrox on -indev "$BUILD_DIR/live-image-amd64.hybrid.iso" \
  -extract /live "$TASK_TMP/live"
unsquashfs -processors 1 -no-progress -d "$TASK_TMP/root" "$TASK_TMP/live/filesystem.squashfs" >/dev/null
ROOT="$TASK_TMP/root"

# Verify the ZFS module matches the packaged kernel.
KERNEL=$(cat "$ROOT/usr/local/share/level1vault/kernel-version")
chroot "$ROOT" modinfo -k "$KERNEL" zfs
VERMAGIC=$(chroot "$ROOT" modinfo -k "$KERNEL" -F vermagic zfs)
[[ $VERMAGIC == "$KERNEL "* ]] || { echo 'ZFS kernel vermagic mismatch' >&2; exit 1; }

# Verify workspace helpers and live account settings.
test -x "$ROOT/usr/local/sbin/workspace-mount"
test -x "$ROOT/usr/local/sbin/workspace-umount"
test -x "$ROOT/usr/local/sbin/workspace-sync"
test -x "$ROOT/usr/local/sbin/workspace-activate"
chroot "$ROOT" /usr/local/sbin/workspace-activate --help >/dev/null
chroot "$ROOT" python3 /usr/local/sbin/workspace-sync --help >/dev/null
grep -qx 'Hello, world' "$ROOT/etc/motd"
[[ $(chroot "$ROOT" id -u simlun) == 2000 && $(chroot "$ROOT" id -g simlun) == 2000 ]]
chroot "$ROOT" visudo -cf /etc/sudoers
chroot "$ROOT" su - simlun -c 'sudo -n id -u' | grep -qx 0
chroot "$ROOT" su - simlun -c 'sudo -n sh -c "command -v workspace-mount && command -v workspace-umount"'

# Verify automatic ZFS imports and mounts are disabled.
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

# Verify image checksums when prepared images are included.
if [[ ${2:-} != --iso-only ]]; then
  test -f "$BUILD_DIR/out/IMAGES-PREPARED"
  test ! -e "$BUILD_DIR/out/READY"
  (cd "$BUILD_DIR/out" && sha256sum -c PREPARED-SHA256SUMS)
fi
printf 'Full Debian Live build inspected successfully: kernel=%s\n' "$KERNEL"
