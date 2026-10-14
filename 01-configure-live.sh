#!/usr/bin/env bash
set -euo pipefail

# Run on Debian 13 amd64, as a regular user, in an empty build directory.
if [[ -e config ]]; then
  echo 'Refusing to overwrite existing config/ directory' >&2
  exit 1
fi

lb config \
  --distribution trixie \
  --architecture amd64 \
  --binary-image iso-hybrid \
  --debian-installer none \
  --archive-areas 'main contrib non-free-firmware' \
  --linux-flavours amd64 \
  --linux-packages 'linux-image linux-headers' \
  --bootappend-live 'boot=live components nopersistence'

mkdir -p config/package-lists config/includes.chroot/usr/local/sbin config/hooks/live
cat > config/package-lists/appliance.list.chroot <<'EOF'
live-boot
live-config
sudo
zfs-dkms
zfsutils-linux
zfs-zed
kmod
udev
util-linux
ca-certificates
curl
vim
tmux
jq
iproute2
iputils-ping
EOF

cat > config/includes.chroot/usr/local/sbin/mount-workspace <<'EOF'
#!/bin/sh
set -eu

POOL=workspace
DATASET=workspace/data
MOUNTPOINT=/mnt/workspace

if [ "$(id -u)" -ne 0 ]; then
    echo 'Run: sudo /usr/local/sbin/mount-workspace' >&2
    exit 1
fi

modprobe zfs
if [ ! -s /etc/hostid ]; then
    zgenhostid
fi
udevadm settle
mkdir -p "$MOUNTPOINT"

if ! zpool list -H -o name 2>/dev/null | grep -Fxq "$POOL"; then
    # Import without mounting datasets; do not force a potentially active pool.
    zpool import -N -d /dev/disk/by-partuuid -o cachefile=none "$POOL"
fi

if ! zfs list -H -o name "$DATASET" >/dev/null 2>&1; then
    echo "Missing ZFS dataset: $DATASET" >&2
    exit 1
fi

if mountpoint -q "$MOUNTPOINT"; then
    if [ "$(findmnt -n -o SOURCE --mountpoint "$MOUNTPOINT")" != "$DATASET" ]; then
        echo "$MOUNTPOINT already contains a different mount; refusing to replace it" >&2
        exit 1
    fi
else
    zfs mount "$DATASET"
fi

findmnt --mountpoint "$MOUNTPOINT"
zpool status "$POOL"
EOF
chmod 0755 config/includes.chroot/usr/local/sbin/mount-workspace

cat > config/hooks/live/90-zfs.hook.chroot <<'EOF'
#!/bin/sh
set -eu

# Fail the live-image build unless an actual ZFS kernel module exists
# for every kernel installed in the live system.
for mods in /lib/modules/*; do
    [ -d "$mods" ] || continue
    version=${mods##*/}
    [ -d "/usr/src/linux-headers-$version" ] || {
        echo "ERROR: missing matching headers for $version" >&2
        exit 1
    }
    dkms autoinstall -k "$version"
    depmod "$version"
    modinfo -k "$version" zfs >/dev/null || {
        echo "ERROR: zfs module not available for $version" >&2
        exit 1
    }
done

# This image is deliberately manual-import-only.
systemctl mask zfs-import-cache.service zfs-import-scan.service zfs-mount.service
# Never clone the build host's ZFS hostid into the live image.
rm -f /etc/hostid
EOF
chmod 0755 config/hooks/live/90-zfs.hook.chroot

echo 'Configured. Now run: sudo lb build'
