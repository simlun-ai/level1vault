#!/usr/bin/env bash
set -euo pipefail
# Run in a fresh build directory; source files stay in the Git checkout.
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
[[ ! -e config ]] || { echo 'Refusing to overwrite config/' >&2; exit 1; }
command -v lb >/dev/null || { echo 'Install Debian 13 live-build first' >&2; exit 1; }


# Require the full repository input prepared by the build wrapper.
REPOSITORY=${LEVEL1VAULT_REPOSITORY:?Full repository bundle directory required}
[[ -f $REPOSITORY/source.bundle && -f $REPOSITORY/commit ]]

# Configure the stateless Debian live system.
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
  --bootappend-live 'boot=live components nopersistence noeject hostname=level1vault username=simlun live-config.nocomponents=user-setup,sudo loglevel=4' \
  "$@"


# Install the workspace helpers and image customization hooks.
mkdir -p config/package-lists config/includes.chroot/usr/local/sbin config/hooks/live
# Existing USB upgrades keep their old GRUB config; apply the console level at runtime too.
mkdir -p config/includes.chroot/etc/sysctl.d
printf 'kernel.printk = 4 4 1 7\n' > config/includes.chroot/etc/sysctl.d/99-level1vault-console.conf
mkdir -p config/includes.chroot/usr/local/lib/level1vault/build
cp "$SOURCE_DIR/stages/01-build-live/packages.list" config/package-lists/appliance.list.chroot
cp -a "$REPOSITORY" config/includes.chroot/usr/local/lib/level1vault/build/repository
cp "$SOURCE_DIR/stages/01-build-live/repository.py" config/includes.chroot/usr/local/lib/level1vault/build/
cp "$SOURCE_DIR/stages/01-build-live/build-packages.list" config/includes.chroot/usr/local/lib/level1vault/build/
cp "$SOURCE_DIR/stages/01-build-live/package-zfs.py" config/includes.chroot/usr/local/lib/level1vault/build/
cp "$SOURCE_DIR/stages/01-build-live/prune-build-packages.py" config/includes.chroot/usr/local/lib/level1vault/build/
cp -a "$SOURCE_DIR/stages/01-build-live/user-config" config/includes.chroot/usr/local/lib/level1vault/build/
install -m 0755 "$SOURCE_DIR/runtime/workspace-mount" config/includes.chroot/usr/local/sbin/workspace-mount
install -m 0755 "$SOURCE_DIR/runtime/workspace-umount" config/includes.chroot/usr/local/sbin/workspace-umount
install -m 0755 "$SOURCE_DIR/runtime/workspace-activate" config/includes.chroot/usr/local/sbin/workspace-activate
install -m 0755 "$SOURCE_DIR/runtime/workspace-sync" config/includes.chroot/usr/local/sbin/workspace-sync
install -m 0755 "$SOURCE_DIR/stages/01-build-live/hooks/50-account.hook.chroot" config/hooks/live/50-account.hook.chroot
install -m 0755 "$SOURCE_DIR/stages/01-build-live/hooks/60-repository.hook.chroot" config/hooks/live/60-repository.hook.chroot
install -m 0755 "$SOURCE_DIR/stages/01-build-live/hooks/90-zfs.hook.chroot" config/hooks/live/90-zfs.hook.chroot
printf 'Configured in %s. Now run: sudo lb build\n' "$PWD"
