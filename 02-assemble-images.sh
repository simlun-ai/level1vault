#!/usr/bin/env bash
set -euo pipefail
# Usage: sudo ./02-assemble-images.sh ISO [NEW_OUTPUT_DIRECTORY]
[[ $EUID -eq 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
[[ $# -ge 1 && $# -le 2 ]] || { echo "Usage: $0 ISO [NEW_OUTPUT_DIRECTORY]" >&2; exit 2; }
for cmd in losetup parted partprobe udevadm mkfs.vfat mount umount mountpoint grub-mkstandalone \
  zpool zfs modprobe blkid unsquashfs dpkg-query realpath truncate sha256sum stat df awk cp sync dpkg mktemp rmdir rm mkdir date touch; do
  command -v "$cmd" >/dev/null || { echo "Missing command: $cmd" >&2; exit 1; }
done
ISO=$(realpath -- "$1")
[[ -f "$ISO" ]] || { echo "No ISO at $ISO" >&2; exit 1; }
# Keep the host toolchain consistent with the live system.
[[ $(dpkg --print-architecture) == amd64 ]] || { echo 'amd64 host required' >&2; exit 1; }
source /etc/os-release
[[ $ID == debian && $VERSION_ID == 13 ]] || { echo 'Debian 13 host required' >&2; exit 1; }
modprobe zfs
VERSIONS=$(zpool --version)
USER_VERSION=$(awk '/^zfs-/ && !/^zfs-kmod-/ {sub(/^zfs-/, ""); print}' <<< "$VERSIONS")
KMOD_VERSION=$(awk '/^zfs-kmod-/ {sub(/^zfs-kmod-/, ""); print}' <<< "$VERSIONS")
[[ -n $USER_VERSION && $USER_VERSION == "$KMOD_VERSION" ]] || {
  echo "ZFS userland/loaded module mismatch: $VERSIONS" >&2; exit 1;
}
if zpool list -H -o name workspace >/dev/null 2>&1; then
  echo 'Pool workspace already imported; refusing' >&2; exit 1
fi
OUT_DIR=$(realpath -m -- "${2:-$PWD/out}")
[[ ! -e "$OUT_DIR" ]] || { echo "Refusing existing output directory: $OUT_DIR" >&2; exit 1; }
mkdir -- "$OUT_DIR"
AIMG="$OUT_DIR/usb-a.img"
BIMG="$OUT_DIR/usb-b.img"
A= B= ISOMNT= ESPMNT= POOL_CREATED=0
cleanup() {
  local rc=$? safe=1
  trap - EXIT
  set +e
  if [[ $POOL_CREATED == 1 ]]; then
    zpool export workspace || { echo 'ERROR: export failed; leaving loop devices attached' >&2; safe=0; }
  fi
  if [[ -n $ESPMNT ]]; then
    if mountpoint -q "$ESPMNT"; then umount "$ESPMNT" || safe=0; fi
    rmdir "$ESPMNT" 2>/dev/null
  fi
  if [[ -n $ISOMNT ]]; then
    if mountpoint -q "$ISOMNT"; then umount "$ISOMNT" || safe=0; fi
    rmdir "$ISOMNT" 2>/dev/null
  fi
  if [[ $safe == 1 ]]; then
    if [[ -n $A ]]; then losetup -d "$A" || safe=0; fi
    if [[ -n $B ]]; then losetup -d "$B" || safe=0; fi
  else
    echo "Inspect mounts/pool before detaching: A=$A B=$B ISO=$ISOMNT ESP=$ESPMNT" >&2
  fi
  if [[ $safe == 0 ]]; then rc=1; fi
  if [[ $rc != 0 ]]; then
    rm -f "$OUT_DIR/READY"
    echo "Assembly failed; $OUT_DIR contains incomplete outputs. Do not flash them." >&2
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
ISOMNT=$(mktemp -d)
ESPMNT=$(mktemp -d)
mount -o loop,ro "$ISO" "$ISOMNT"
SQUASH="$ISOMNT/live/filesystem.squashfs"
[[ -f $SQUASH ]] || { echo 'Missing filesystem.squashfs' >&2; exit 1; }
TARGET_ZFS=$(unsquashfs -cat "$SQUASH" usr/local/share/level1vault/zfsutils-version)
HOST_ZFS=$(dpkg-query -W -f='${Version}' zfsutils-linux)
[[ $HOST_ZFS == "$TARGET_ZFS" ]] || {
  echo "ZFS package mismatch: host=$HOST_ZFS live=$TARGET_ZFS; use the same Debian package version" >&2; exit 1;
}
KERNEL=$(unsquashfs -cat "$SQUASH" usr/local/share/level1vault/kernel-version)
[[ $KERNEL =~ ^[0-9][a-zA-Z0-9.+_-]*$ ]] || { echo 'Invalid kernel metadata' >&2; exit 1; }
VMLINUX="$ISOMNT/live/vmlinuz-$KERNEL"
INITRD="$ISOMNT/live/initrd.img-$KERNEL"
[[ -f $VMLINUX ]] || VMLINUX="$ISOMNT/live/vmlinuz"
[[ -f $INITRD ]] || INITRD="$ISOMNT/live/initrd.img"
[[ -f $VMLINUX && -f $INITRD ]] || { echo 'Missing selected live kernel/initrd' >&2; exit 1; }
# FAT32 file limit and actual formatted ESP capacity are separate constraints.
for file in "$SQUASH" "$VMLINUX" "$INITRD"; do
  [[ $(stat -Lc %s "$file") -lt 4294967296 ]] || { echo "FAT32 file too large: $file" >&2; exit 1; }
done
REQUIRED=$(( $(stat -Lc %s "$SQUASH") + $(stat -Lc %s "$VMLINUX") + $(stat -Lc %s "$INITRD") + 64*1024*1024 ))
truncate -s 12G "$AIMG"
truncate -s 12G "$BIMG"
A=$(losetup --find --show --partscan "$AIMG")
B=$(losetup --find --show --partscan "$BIMG")
for dev in "$A" "$B"; do
  parted -s -a optimal "$dev" mklabel gpt mkpart EFI fat32 1MiB 4097MiB \
    set 1 esp on mkpart WORKSPACE 4097MiB 100%
  partprobe "$dev"
done
udevadm settle
for p in "${A}p1" "${A}p2" "${B}p1" "${B}p2"; do
  [[ -b $p ]] || { echo "Partition node missing: $p" >&2; exit 1; }
done
APART=$(blkid -s PARTUUID -o value "${A}p2")
BPART=$(blkid -s PARTUUID -o value "${B}p2")
[[ -n $APART && -n $BPART && $APART != "$BPART" ]] || { echo 'Invalid/duplicate PARTUUIDs' >&2; exit 1; }
udevadm settle
[[ -b /dev/disk/by-partuuid/$APART && -b /dev/disk/by-partuuid/$BPART ]] || {
  echo 'PARTUUID symlinks missing; check udev' >&2; exit 1;
}
for name in A B; do
  if [[ $name == A ]]; then dev=$A; else dev=$B; fi
  mkfs.vfat -F 32 -n "LIVE_$name" "${dev}p1"
  mount "${dev}p1" "$ESPMNT"
  AVAILABLE=$(df -B1 --output=avail "$ESPMNT" | awk 'NR==2 {print $1}')
  [[ $REQUIRED -lt $AVAILABLE ]] || { echo 'Live payload does not fit 4 GiB ESP' >&2; exit 1; }
  mkdir -p "$ESPMNT/live" "$ESPMNT/boot/grub" "$ESPMNT/EFI/BOOT"
  # Dereference aliases and never copy Unix permissions/symlinks to FAT.
  cp -L -- "$VMLINUX" "$ESPMNT/live/vmlinuz"
  cp -L -- "$INITRD" "$ESPMNT/live/initrd.img"
  cp -- "$SQUASH" "$ESPMNT/live/filesystem.squashfs"
  unsquashfs -cat "$SQUASH" usr/local/share/level1vault/packages.tsv > "$ESPMNT/live/packages.tsv"
  # Preserve optional live-boot medium UUID markers embedded in the initramfs.
  if [[ -d $ISOMNT/.disk ]]; then cp -rL -- "$ISOMNT/.disk" "$ESPMNT/"; fi
  uuid=$(blkid -s UUID -o value "${dev}p1")
  [[ -n $uuid ]] || { echo 'Missing ESP UUID' >&2; exit 1; }
  if [[ $name == A ]]; then AUUID=$uuid; else BUUID=$uuid; fi
  cat > "$ESPMNT/boot/grub/grub.cfg" <<CFG
set default=0
set timeout=3
menuentry 'level1vault Debian Live (stateless)' {
    search --no-floppy --fs-uuid --set=root $uuid
    linux /live/vmlinuz boot=live components nopersistence noeject hostname=level1vault live-media=/dev/disk/by-uuid/$uuid live-media-timeout=30
    initrd /live/initrd.img
}
CFG
  cat > "$OUT_DIR/embedded-$name.cfg" <<CFG
search --no-floppy --fs-uuid --set=root $uuid
set prefix=(\$root)/boot/grub
configfile /boot/grub/grub.cfg
CFG
  # No grub-install host disk probing, no NVRAM, no dependency on the other ESP.
  grub-mkstandalone --format=x86_64-efi \
    --modules='part_gpt fat search search_fs_uuid normal configfile linux' \
    --locales='' --fonts='' \
    --output="$ESPMNT/EFI/BOOT/BOOTX64.EFI" \
    "boot/grub/grub.cfg=$OUT_DIR/embedded-$name.cfg"
  [[ -s $ESPMNT/EFI/BOOT/BOOTX64.EFI ]] || { echo 'Missing EFI loader' >&2; exit 1; }
  sync
  umount "$ESPMNT"
done
[[ $AUUID != "$BUUID" ]] || { echo 'Duplicate FAT UUIDs' >&2; exit 1; }
# Exactly one pool across both images; never initialize the members separately.
zpool create -o ashift=12 -o cachefile=none -O mountpoint=none -O canmount=off \
  workspace mirror "/dev/disk/by-partuuid/$APART" "/dev/disk/by-partuuid/$BPART"
POOL_CREATED=1
zfs create -u -o mountpoint=/mnt/workspace -o canmount=noauto \
  -o compression=lz4 -o atime=off -o org.openzfs.systemd:ignore=on workspace/data
{
  date -u +build_time=%FT%TZ
  printf 'kernel=%s\nzfsutils_package=%s\nesp_a_uuid=%s\nesp_b_uuid=%s\nzfs_a_partuuid=%s\nzfs_b_partuuid=%s\n' \
    "$KERNEL" "$TARGET_ZFS" "$AUUID" "$BUUID" "$APART" "$BPART"
  printf 'pool_guid=%s\n' "$(zpool get -H -o value guid workspace)"
  dpkg-query -W live-build grub-efi-amd64-bin grub-common zfsutils-linux
  sha256sum "$ISO"
  zpool status -gP workspace
} > "$OUT_DIR/BUILD-INFO.txt"
zpool export workspace
POOL_CREATED=0
umount "$ISOMNT"
# Absolute image paths avoid depending on the shell's working directory during verification.
sha256sum "$AIMG" "$BIMG" > "$OUT_DIR/SHA256SUMS"
touch "$OUT_DIR/READY"
printf 'Images ready in %s (12 GiB each). See BUILD-INFO.txt and SHA256SUMS.\n' "$OUT_DIR"
