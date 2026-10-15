#!/usr/bin/env bash
set -euo pipefail
# Run in a fresh build directory; source files stay in the Git checkout.
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
[[ ! -e config ]] || { echo 'Refusing to overwrite config/' >&2; exit 1; }
command -v lb >/dev/null || { echo 'Install Debian 13 live-build first' >&2; exit 1; }


# Configure the stateless Debian live system.
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
  --bootappend-live 'boot=live components nopersistence noeject hostname=level1vault username=simlun live-config.nocomponents=user-setup,sudo' \
  "$@"


# Install the workspace helpers and image customization hooks.
mkdir -p config/package-lists config/includes.chroot/usr/local/sbin config/hooks/live
cp "$SOURCE_DIR/stages/01-build-live/packages.list" config/package-lists/appliance.list.chroot
install -m 0755 "$SOURCE_DIR/runtime/workspace-mount" config/includes.chroot/usr/local/sbin/workspace-mount
install -m 0755 "$SOURCE_DIR/runtime/workspace-umount" config/includes.chroot/usr/local/sbin/workspace-umount
install -m 0755 "$SOURCE_DIR/runtime/workspace-activate" config/includes.chroot/usr/local/sbin/workspace-activate
install -m 0755 "$SOURCE_DIR/runtime/workspace-sync" config/includes.chroot/usr/local/sbin/workspace-sync
install -m 0755 "$SOURCE_DIR/stages/01-build-live/hooks/50-account.hook.chroot" config/hooks/live/50-account.hook.chroot
install -m 0755 "$SOURCE_DIR/stages/01-build-live/hooks/90-zfs.hook.chroot" config/hooks/live/90-zfs.hook.chroot
printf 'Configured in %s. Now run: sudo lb build\n' "$PWD"
