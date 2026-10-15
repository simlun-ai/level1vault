# Build reference

[Documentation index](README.md)

## Workstation requirements

Use local, rootful Docker Engine on amd64 Linux. Docker Desktop, remote Docker daemons, rootless Docker and cross-architecture builds are not supported by the wrapper. The first full CI build took about 17 minutes on its runner; workstation time varies. Have at least 40 GiB of free working space; more is useful for keeping multiple build versions. Use a native Linux filesystem for the build directory. From an existing checkout, run `git switch main` and `git pull --ff-only` before the first build.

If Docker is not installed, Ubuntu's package is sufficient for this workflow:

```bash
sudo apt update
sudo apt install -y docker.io git make gdisk parted util-linux udev kmod python3 \
    qemu-system-x86 ovmf
sudo systemctl enable --now docker
# Your existing ZFS installation should already provide zpool/zfs/modprobe.
sudo modprobe zfs
zpool --version
sudo zpool list
sudo docker info
```

Keep Ubuntu's own ZFS packages and kernel. Do not replace them with Debian packages. If the workstation already has an imported pool named `workspace-1`, initialization refuses to proceed; use another host or resolve that name conflict deliberately. Other existing host pools are left alone.

The live-build container runs with `--privileged` because its chroot build needs mounts and device access. This grants host-root-equivalent privileges: use trusted source and build configuration. The wrapper mounts the checkout read-only and one build directory read-write, with private mount propagation. It does not bind the host `/dev`, `/run/udev`, `/lib/modules`, or Docker socket; privileged Docker can still expose host devices. No physical-device operations or ZFS pool commands are part of the container build scripts.

## Build the live system and boot images

```bash
git clone --branch main https://github.com/simlun-ai/level1vault.git
cd level1vault
make build
```

The wrapper builds `level1vault-builder:trixie`, then runs live-build and file-only disk assembly. It refuses an existing build directory, preventing accidental reuse of stale chroots/configuration. A later build can use `"$PWD/build-next"` instead.

Successful output:

```text
build/
  builder-image.json                # Docker toolchain identity
  build.log
  live-image-amd64.hybrid.iso        # intermediate ISO
  out/
    usb-a.img
    usb-b.img
    images.json                     # target kernel/ZFS versions and per-image identities
    BUILD-INFO.txt
    PREPARED-SHA256SUMS
    IMAGES-PREPARED
```

`IMAGES-PREPARED` means the OS and bootloader are present, but both data partitions are **empty**. It is not the final ready-to-flash marker. Artifacts may be root-owned; readable files can be inspected normally. To report a build failure, include the final 100 lines of `build/build.log` and the complete error printed by the wrapper. Do not recursively chown or remove a failed chroot until its mounts have been checked.

For configuration customization, pass extra `lb config` options after the build directory. Do not override kernel flavour, architecture or boot layout without updating the associated checks. For frozen package inputs and base image digest selection, see [reproducibility](#reproducibility).

## Initialize a pair

Exit any build container; run this in the Ubuntu workstation shell:

```bash
make initialize PAIR=1 ACTIVE=1
# Enter/confirm the workspace encryption passphrase at ZFS's stdin prompt.
test -f build/out/READY
cat build/out/WORKSPACE-INFO.txt
(cd build/out && sha256sum -c SHA256SUMS)
```

This verifies the prepared checksums, refuses attached/previously initialized images, attaches both files using loop devices, verifies their data PARTUUIDs and blank signatures, and creates **one pool across both partitions**. Nothing is created on physical USBs at this stage.

The pool has `ashift=12`, `cachefile=none`, an unmountable root dataset, and an AES-256-GCM encrypted `workspace-1/data` with `keyformat=passphrase`, `keylocation=prompt`, `canmount=noauto`, LZ4 compression and the expected mountpoint. It briefly mounts the new dataset beneath a private temporary altroot to set the filesystem root to `2000:2000`, mode `0750`, without covering a host mountpoint. The passphrase is read directly by ZFS from stdin and is never saved in the image or manifest. The script exports the pool and detaches loops before writing `READY` and final image checksums. It never imports/exports unrelated host pools.

To support Ubuntu 22.04's OpenZFS 2.1 and Debian 13's newer ZFS, creation uses `zpool create -d` and enables **`lz4_compress`, `encryption` and encryption's required dependencies** (including `extensible_dataset` for resumable receives). These features are supported by OpenZFS 2.1. This omits newer optional pool features. `zpool status` may report that supported features are disabled; that message is expected. **Do not run `zpool upgrade`** if you need continued Ubuntu 22.04 access. Exact Ubuntu/Debian package equality is no longer required.

If initialization fails, `INITIALIZING` remains and reruns are refused. Check `sudo zpool status workspace-1` and `sudo losetup -j /absolute/path/to/usb-a.img` (and B). Failed export retains loops rather than detaching an active pool. Resolve/export the pool and detach only this build's loops before removing incomplete artifacts. For an unused, failed initial build, reassemble from the ISO into a **new** output directory. Never discard images containing data you need.

## Reproducibility

Default Debian mirrors and the `debian:trixie` tag move over time. The wrapper records the builder image identity in `build/builder-image.json`; the chroot records package/kernel versions, and the host records its ZFS versions and pool GUID in `WORKSPACE-INFO.txt`.

For repeatable package inputs, select a verified Debian base image digest and Debian archive snapshot timestamp. Pin both the Dockerfile's toolchain package sources and live-build's bootstrap/chroot/security mirrors; pinning only the Docker image tag or only live-build mirrors is insufficient. The Dockerfile accepts `DEBIAN_IMAGE`; changing its APT sources to a historical snapshot is an explicit release configuration task.

Example live-build arguments (replace the timestamp and digest with verified values):

```bash
snapshot=REPLACE_WITH_VERIFIED_TIMESTAMP
sudo env DEBIAN_IMAGE=debian:trixie@sha256:VERIFIED_DIGEST \
  ./build-container.sh "$PWD/build-pinned" \
  --mirror-bootstrap "https://snapshot.debian.org/archive/debian/$snapshot/" \
  --mirror-chroot "https://snapshot.debian.org/archive/debian/$snapshot/" \
  --mirror-chroot-security "https://snapshot.debian.org/archive/debian-security/$snapshot/" \
  --apt-options '--yes -o Acquire::Check-Valid-Until=false'
```

Keep archive signature verification enabled. Expiry bypass is only for the selected historical signed snapshot. Verify trixie, trixie-updates and trixie-security are available. Record the Dockerfile and host ZFS environment with each release. New disk assembly deliberately generates new filesystem/GPT identities and host initialization creates a new pool GUID: raw images are not bit-identical reproducible outputs.
