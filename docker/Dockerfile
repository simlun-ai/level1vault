# Pin DEBIAN_IMAGE to a digest when freezing a release toolchain.
ARG DEBIAN_IMAGE=debian:trixie
FROM ${DEBIAN_IMAGE}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    live-build debootstrap xorriso squashfs-tools dosfstools mtools \
    isolinux syslinux-common grub-efi-amd64-bin grub-common grub2-common \
    gdisk util-linux ca-certificates python3 file cpio rsync kmod apt-utils \
    && rm -rf /var/lib/apt/lists/*
# No host kernel headers, zfsutils or zfs-dkms in the container toolchain.
# The live-build chroot installs its own Debian kernel/headers and ZFS packages.
WORKDIR /build
