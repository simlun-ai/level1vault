#!/usr/bin/env bash
set -euo pipefail

# Run as root on Debian 13. Requires the matching ZFS kernel module on
# the build host and the ISO output from 01-configure-live.sh + lb build.
# Usage: sudo ./02-assemble-images.sh /absolute/path/live-image-amd64.hybrid.iso

[[ $EUID -eq 0 ]] || { echo 'Run this script with sudo' >&2; exit 1; }
[[ $# -eq 1 ]] || { echo "Usage: $0 /absolute/path/live-image-amd64.hybrid.iso" >&2; exit 2; }
ISO=$(realpath "$1")
[[ -f "$ISO" ]] || { echo "No ISO at $ISO" >&2; exit 1; }

for cmd in losetup parted partprobe udevadm mkfs.vfat mount umount grub-install zpool zfs modprobe blkid findmnt; do
  command -v "$cmd" >/dev/null || { echo "Missing command: $cmd" >&2; exit 1; }
done
modprobe zfs
if zpool list -H -o name 2>/dev/null | grep -Fxq workspace; then
  echo 'ZFS pool workspace is already imported; refusing to continue.' >&2
  exit 1
fi

OUT_DIR=$(pwd)
AIMG="$OUT_DIR/usb-a.img"
BIMG="$OUT_DIR/usb-b.img"
for img in "$AIMG" "$BIMG"; do
  [[ ! -e "$img" ]] || { echo "Refusing to overwrite $img" >&2; exit 1; }
done

# Intentionally sparse images, 12 GiB each: 4 GiB ESP/boot and ~8 GiB ZFS.
truncate -s 12G "$AIMG"
truncate -s 12G "$BIMG"
A= B= ISOMNT= ESPMNT= POOL_CREATED=0
cleanup() {
  set +e
  if [[ -n "$ESPMNT" ]]; then mountpoint -q "$ESPMNT" && umount "$ESPMNT"; rmdir "$ESPMNT"; fi
  if [[ -n "$ISOMNT" ]]; then mountpoint -q "$ISOMNT" && umount "$ISOMNT"; rmdir "$ISOMNT"; fi
  # Cleanly export a pool created by this run before detaching loop devices.
  if [[ "$POOL_CREATED" == 1 ]]; then zpool export workspace || echo "WARNING: export failed; investigate before reusing images" >&2; fi
  [[ -n "$A" ]] && losetup -d "$A"
  [[ -n "$B" ]] && losetup -d "$B"
}
trap cleanup EXIT

A=$(losetup --find --show --partscan "$AIMG")
B=$(losetup --find --show --partscan "$BIMG")

for dev in "$A" "$B"; do
  parted -s -a optimal "$dev" \
    mklabel gpt \
    mkpart EFI fat32 1MiB 4097MiB \
    set 1 esp on \
    mkpart WORKSPACE 4097MiB 100%
  partprobe "$dev"
done
udevadm settle
for p in "${A}p1" "${A}p2" "${B}p1" "${B}p2"; do
  [[ -b "$p" ]] || { echo "Partition node not found: $p" >&2; exit 1; }
done

mkfs.vfat -F 32 -n LIVE_A "${A}p1"
mkfs.vfat -F 32 -n LIVE_B "${B}p1"

ISOMNT=$(mktemp -d)
ESPMNT=$(mktemp -d)
mount -o loop,ro "$ISO" "$ISOMNT"
for file in live/vmlinuz live/initrd.img live/filesystem.squashfs; do
  [[ -f "$ISOMNT/$file" ]] || { echo "Expected ISO file missing: $file" >&2; exit 1; }
done

for name in A B; do
  if [[ "$name" == A ]]; then dev=$A; else dev=$B; fi
  mount "${dev}p1" "$ESPMNT"
  mkdir -p "$ESPMNT/live" "$ESPMNT/boot/grub"
  cp -a "$ISOMNT/live/." "$ESPMNT/live/"
  uuid=$(blkid -s UUID -o value "${dev}p1")
  cat > "$ESPMNT/boot/grub/grub.cfg" <<EOF
set default=0
set timeout=3

menuentry 'Debian Live (stateless)' {
    insmod part_gpt
    insmod fat
    search --no-floppy --fs-uuid --set=root $uuid
    linux /live/vmlinuz boot=live components nopersistence live-media=/dev/disk/by-uuid/$uuid live-media-timeout=15
    initrd /live/initrd.img
}
EOF
  grub-install \
    --target=x86_64-efi \
    --efi-directory="$ESPMNT" \
    --boot-directory="$ESPMNT/boot" \
    --removable --no-nvram
  [[ -f "$ESPMNT/EFI/BOOT/BOOTX64.EFI" ]] || { echo "EFI binary missing for $name" >&2; exit 1; }
  sync
  umount "$ESPMNT"
done
umount "$ISOMNT"

# The ZFS pool is created ONCE, across one partition from EACH image.
# The distinct GPT partition UUIDs survive dd and are discoverable on boot.
zpool create \
  -o ashift=12 \
  -o cachefile=none \
  -O mountpoint=none \
  -O canmount=off \
  workspace mirror "${A}p2" "${B}p2"
POOL_CREATED=1
zfs create -u -o mountpoint=/mnt/workspace -o canmount=on \
  -o compression=lz4 -o atime=off workspace/data

zpool status workspace
zfs list -o name,mountpoint,canmount workspace/data
zpool export workspace
POOL_CREATED=0

echo 'Images ready:'
ls -lh "$AIMG" "$BIMG"
echo 'Write the two images to two separate USB devices only after verifying device identities.'
