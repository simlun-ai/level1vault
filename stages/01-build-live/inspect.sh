#!/usr/bin/env bash
set -euo pipefail

# Validate the requested build inspection scope.
[[ $# -ge 1 && $# -le 2 && ${2:---iso-only} == --iso-only ]] || { echo "Usage: $0 BUILD_DIRECTORY [--iso-only]" >&2; exit 2; }
BUILD_DIR=$(realpath -- "$1")
TASK_TMP=$(mktemp -d)
trap 'rm -rf -- "$TASK_TMP"' EXIT

# Require an EFI boot entry and reject legacy BIOS boot entries in the ISO.
BOOT_REPORT=$(xorriso -indev "$BUILD_DIR/live-image-amd64.hybrid.iso" -report_el_torito plain 2>&1)
grep -Eq '^El Torito boot img :.*UEFI' <<< "$BOOT_REPORT"
if grep -Eq '^El Torito boot img :.*BIOS' <<< "$BOOT_REPORT"; then
  echo 'Unexpected legacy BIOS boot entry in UEFI-only image' >&2; exit 1
fi

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

# Verify independent module ownership and the absence of the build toolchain.
MODULE=$(chroot "$ROOT" modinfo -k "$KERNEL" -n zfs)
CANONICAL=$(chroot "$ROOT" readlink -f "$MODULE")
[[ $CANONICAL == "/usr/lib/modules/$KERNEL/extra/level1vault/"* ]]
chroot "$ROOT" dpkg-query -S "$CANONICAL" | grep -q '^level1vault-zfs-modules:'
chroot "$ROOT" modprobe --show-depends --set-version "$KERNEL" zfs
for tool in cc gcc g++ c++ clang make dkms; do
  if chroot "$ROOT" sh -c "command -v \"\$1\"" sh "$tool"; then
    echo "Unexpected build tool: $tool" >&2; exit 1
  fi
done
[[ -z $(find "$ROOT/usr/src" -mindepth 1 -print -quit) ]]
test ! -e "$ROOT/usr/local/lib/level1vault/build"
chroot "$ROOT" dpkg --audit

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
