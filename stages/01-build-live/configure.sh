#!/usr/bin/env bash
set -euo pipefail
# Run in a fresh build directory; source files stay in the Git checkout.
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
[[ ! -e config ]] || { echo 'Refusing to overwrite config/' >&2; exit 1; }
command -v lb >/dev/null || { echo 'Install Debian 13 live-build first' >&2; exit 1; }


# Require the full repository input prepared by the build wrapper.
REPOSITORY=${LEVEL1VAULT_REPOSITORY:?Full repository bundle directory required}
[[ -f $REPOSITORY/source.bundle && -f $REPOSITORY/commit ]]

# Configure the stateless Debian live system with the shared boot policy.
BOOT_PARAMETERS=$(cat "$SOURCE_DIR/common/boot-parameters.txt")
lb config \
  --ignore-system-defaults \
  --mode debian \
  --distribution trixie \
  --architecture amd64 \
  --binary-image iso-hybrid \
  --bootloaders grub-efi \
  --memtest none \
  --win32-loader false \
  --uefi-secure-boot disable \
  --debian-installer none \
  --archive-areas 'main contrib non-free-firmware' \
  --apt-recommends false \
  --chroot-filesystem squashfs \
  --initramfs live-boot \
  --initsystem systemd \
  --linux-flavours amd64 \
  --linux-packages linux-image \
  --bootappend-live "$BOOT_PARAMETERS" \
  "$@"


# Install the workspace helpers and image customization hooks.
mkdir -p config/package-lists config/includes.chroot/usr/local/sbin config/hooks/live
mkdir -p config/includes.chroot/usr/local/lib/level1vault/build
cp "$SOURCE_DIR/stages/01-build-live/packages.list" config/package-lists/appliance.list.chroot
cp -a "$REPOSITORY" config/includes.chroot/usr/local/lib/level1vault/build/repository
cp "$SOURCE_DIR/stages/01-build-live/repository.py" config/includes.chroot/usr/local/lib/level1vault/build/
cp "$SOURCE_DIR/stages/01-build-live/build-packages.list" config/includes.chroot/usr/local/lib/level1vault/build/
cp "$SOURCE_DIR/stages/01-build-live/package-zfs.py" config/includes.chroot/usr/local/lib/level1vault/build/
cp "$SOURCE_DIR/stages/01-build-live/prune-build-packages.py" config/includes.chroot/usr/local/lib/level1vault/build/
cp -a "$SOURCE_DIR/stages/01-build-live/user-config" config/includes.chroot/usr/local/lib/level1vault/build/
install -m 0644 "$SOURCE_DIR/common/contracts.py" config/includes.chroot/usr/local/lib/level1vault/contracts.py
install -m 0644 "$SOURCE_DIR/common/git_bundles.py" config/includes.chroot/usr/local/lib/level1vault/git_bundles.py
install -m 0755 "$SOURCE_DIR/runtime/workspace-transport-import-git-bundles" config/includes.chroot/usr/local/sbin/workspace-transport-import-git-bundles
install -m 0644 "$SOURCE_DIR/common/transport.py" config/includes.chroot/usr/local/lib/level1vault/transport.py
install -m 0755 "$SOURCE_DIR/runtime/workspace-transport-import" config/includes.chroot/usr/local/sbin/workspace-transport-import
install -m 0644 "$SOURCE_DIR/common/member_recovery.py" config/includes.chroot/usr/local/lib/level1vault/member_recovery.py
install -m 0644 "$SOURCE_DIR/common/usb_devices.py" config/includes.chroot/usr/local/lib/level1vault/usb_devices.py
install -m 0755 "$SOURCE_DIR/runtime/workspace-replace" config/includes.chroot/usr/local/sbin/workspace-replace
install -m 0755 "$SOURCE_DIR/runtime/workspace-discover" config/includes.chroot/usr/local/sbin/workspace-discover
install -m 0755 "$SOURCE_DIR/runtime/start" config/includes.chroot/usr/local/sbin/start
install -m 0755 "$SOURCE_DIR/runtime/workspace-home" config/includes.chroot/usr/local/sbin/workspace-home
install -m 0755 "$SOURCE_DIR/runtime/workspace-mount" config/includes.chroot/usr/local/sbin/workspace-mount
install -m 0755 "$SOURCE_DIR/runtime/workspace-umount" config/includes.chroot/usr/local/sbin/workspace-umount
install -m 0755 "$SOURCE_DIR/runtime/workspace-activate" config/includes.chroot/usr/local/sbin/workspace-activate
install -m 0755 "$SOURCE_DIR/runtime/workspace-prune" config/includes.chroot/usr/local/sbin/workspace-prune
install -m 0755 "$SOURCE_DIR/runtime/workspace-sync" config/includes.chroot/usr/local/sbin/workspace-sync
install -m 0755 "$SOURCE_DIR/runtime/workspace-rsync" config/includes.chroot/usr/local/sbin/workspace-rsync
install -m 0755 "$SOURCE_DIR/stages/01-build-live/hooks/50-account.hook.chroot" config/hooks/live/50-account.hook.chroot
install -m 0755 "$SOURCE_DIR/stages/01-build-live/hooks/60-repository.hook.chroot" config/hooks/live/60-repository.hook.chroot
install -m 0755 "$SOURCE_DIR/stages/01-build-live/hooks/90-zfs.hook.chroot" config/hooks/live/90-zfs.hook.chroot
printf 'Configured in %s. Now run: sudo lb build\n' "$PWD"

