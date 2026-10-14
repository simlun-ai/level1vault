#!/usr/bin/env bash
set -euo pipefail
# Run in a fresh build directory; source files stay in the Git checkout.
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ ! -e config ]] || { echo 'Refusing to overwrite config/' >&2; exit 1; }
command -v lb >/dev/null || { echo 'Install Debian 13 live-build first' >&2; exit 1; }

lb config \
  --ignore-system-defaults \
  --mode debian \
  --distribution trixie \
  --architecture amd64 \
  --binary-image iso-hybrid \
  --bootloaders 'syslinux grub-efi' \
  --uefi-secure-boot disable \
  --debian-installer none \
  --archive-areas 'main contrib non-free-firmware' \
  --apt-recommends false \
  --chroot-filesystem squashfs \
  --initramfs live-boot \
  --initsystem systemd \
  --linux-flavours amd64 \
  --linux-packages 'linux-image linux-headers' \
  --bootappend-live 'boot=live components nopersistence noeject hostname=level1vault' \
  "$@"

mkdir -p config/package-lists config/includes.chroot/usr/local/sbin config/hooks/live
cp "$SOURCE_DIR/live/packages.list.chroot" config/package-lists/appliance.list.chroot
install -m 0755 "$SOURCE_DIR/live/mount-workspace" config/includes.chroot/usr/local/sbin/mount-workspace
install -m 0755 "$SOURCE_DIR/live/90-zfs.hook.chroot" config/hooks/live/90-zfs.hook.chroot
printf 'Configured in %s. Now run: sudo lb build\n' "$PWD"
