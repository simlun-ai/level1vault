# Build reference

[Documentation index](README.md)

## Workstation requirements

Use a Linux amd64 workstation with working ZFS and local, rootful Docker Engine with the Buildx plugin. Ubuntu 22.04 is the tested host example; it is not a distribution requirement. Docker Desktop, remote Docker daemons, rootless Docker and cross-architecture builds are not supported by the wrapper. The previous container workflow took about 17 minutes in CI; BuildKit timings and storage use need validation. Have at least 80 GiB of free working space; more is useful for keeping multiple build versions. Use a native Linux filesystem for the build directory. From an existing checkout, run `git switch main` and `git pull --ff-only` before the first build.

Install [Docker Engine with Buildx](https://docs.docker.com/engine/install/) and verify `docker buildx version`. Your normal user must be able to access the local Docker daemon (for example, using Docker's documented Linux post-installation setup). Run `make` without sudo; individual host stages request it. Install the remaining host utilities using your distribution’s packages. For example, on Ubuntu 22.04:

```bash
sudo apt update
sudo apt install -y git make gdisk parted fdisk dosfstools util-linux udev kmod python3 \
    qemu-system-x86 ovmf
sudo systemctl enable --now docker
# Your existing ZFS installation should already provide zpool/zfs/modprobe.
sudo modprobe zfs
zpool --version
sudo zpool list
docker info
docker buildx version
```

Keep your host distribution's own ZFS packages and kernel. Do not replace them with Debian packages. If the workstation already has an imported pool named `workspace-1`, initialization refuses to proceed; use another host or resolve that name conflict deliberately. Other existing host pools are left alone.

The [BuildKit pipeline](BUILDKIT.md) confines the required privileged RUN to live-build. It exports artifacts from scratch stages; ZFS initialization stays on the host. Use trusted source: the live-build entitlement grants privileges equivalent to the previous privileged container.

## Check host prerequisites

Run `make check-host` after cloning. It checks utilities for building, initialization, USB writing/upgrades, local tests, container tests, QEMU (including mtools, Python pexpect and OVMF), and disposable-VM ZFS integration. Missing tools are grouped by workflow with a combined `sudo apt install -y ...` command. It also checks Docker/Buildx access and ZFS readiness, without installing packages, loading modules, running tests or importing pools. A failure means a dependency or readiness problem remains; Docker access/kernel problems have separate instructions. Docker CE uses `docker-ce`/`docker-buildx-plugin`; distribution Docker uses `docker.io`/`docker-buildx`. For custom firmware paths, use `python3 pipeline/check-host.py --ovmf-code PATH --ovmf-vars PATH` and pass the same files to QEMU acceptance with `QEMU_ARGS='--code PATH --vars PATH'`.

## Prepare N complete pairs in one command

From a fresh full Git checkout on the prepared workstation:

```bash
make prepare-pairs N=1            # one complete active pair
# Or, for one active pair and two populated replicas:
make prepare-pairs N=3
```

`N` is mandatory and must be a positive integer. The target authenticates with sudo once, checks host prerequisites, and prompts for the new workspace encryption passphrase twice **before** building. The host coordinator retains privilege so sudo does not expire midway through the build, but runs Buildx as the invoking user with that user’s Docker configuration. Host-created markers and reports are published with the invoking UID/GID, including on handled failures. Direct root invocation with `SUDO=` retains root ownership. The passphrase stays in memory and is passed to the first initializer through stdin; it is never saved in arguments, environment, images, or logs. All later subprocesses have closed stdin and require no operator input.

The command builds one ISO, inspects it, assembles unique pairs numbered 1 through N, initializes pair 1 as active and the others as replicas, and populates every replica from a shared raw encrypted checkpoint. Only the new images are attached on host loops. Imports use their recorded pool GUIDs and private device search directories. The command exports only its own pools, detaches their loops, and publishes readiness for every pair before reporting success. Replicas inherit pair 1's workspace passphrase.

On success, write each pair using [the USB target](../stages/05-write-usb/README.md):

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
make write-usb PAIR=1 DISK_A=/dev/sda DISK_B=/dev/sdb
# Repeat with PAIR=2, PAIR=3, etc., identifying new destination disks each time.
```

No initial sync is needed after flashing. The batch starts with an empty workspace; it does not copy data from an existing deployment. Disconnect other copies of these pool identities and resolve any imported `workspace-1` through `workspace-N` before starting. For adding replicas to an existing deployment, use [additional pairs](../README.md#add-more-pairs) instead.

The output defaults to `build/default/pairs/ID/`. Existing build directories are refused; use `BUILD_DIR=build-next` for a new batch and pass that same setting when writing. `FRESH=1`, `DEBIAN_IMAGE`, and `BUILDX_BUILDER` retain their usual meanings. Budget up to 24 GiB per pair plus build workspace. This command does not run QEMU or write physical disks.

If any step fails, preparation stops, leaves partial artifacts for inspection, and retains `PREPARING-PAIRS` once the ISO build has finished. Do not flash incomplete batches. Failed exports retain the associated loops; inspect pool GUIDs and loop backing files before recovery. Never blindly delete partial outputs or rerun initialization over them. Start a replacement batch in a new directory only after resolving leftover resources. Removing a marker does not repair a failed run.

## Select a build directory and add a pair

`BUILD_DIR` defaults to `build/default`. Each run holds its ISO under `live/`, images under `pairs/ID/`, and acceptance reports under `validation/`. Use a different directory to preserve older images and to separate batches or OS releases. Use the same setting when writing, testing or adding pairs from that run:

```bash
make prepare-pairs N=1 BUILD_DIR=build/initial
make add-pair PAIR=2 BUILD_DIR=build/initial
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
# Identify the two destination whole disks before this destructive write.
make write-usb PAIR=2 BUILD_DIR=build/initial DISK_A=/dev/sda DISK_B=/dev/sdb
```

`add-pair` reuses the existing ISO, authenticates with sudo before assembly, creates fresh image identities and initializes an empty replica in one command. `PAIR` must be explicit and unused; `ACTIVE=1` is refused. Initialization still refuses partial or previously initialized output. A failure preserves diagnostics and any initialization markers for inspection. Populate the new replica with `workspace-sync` after flashing, as described in [additional pairs](../README.md#add-more-pairs). The original ISO does not incorporate later source changes; prepare a new OS release for those changes using [the two-step upgrade workflow](UPGRADES.md).

## Build the live system and boot images

```bash
git clone --branch main https://github.com/simlun-ai/level1vault.git
cd level1vault
make build
```

The wrapper creates a dedicated BuildKit builder, builds the ISO and file-only image stages, then exports their artifacts. It refuses an existing output directory. A later build can use `make build BUILD_DIR=build-next`; add `FRESH=1` to refresh packages rather than reuse the cached ISO.

Successful output:

| Output | Purpose |
| --- | --- |
| `build/default/live/live-image-amd64.hybrid.iso` | Stage 1 live ISO |
| `build/default/live/live-build.log` | Successful live-build log |
| `build/default/pairs/1/usb-a.img`, `usb-b.img` | Stage 2 bootable pair |
| `build/default/pairs/1/images.json` | Target versions and per-image identities |
| `build/default/pairs/1/BUILD-INFO.txt` | Assembly record |
| `build/default/pairs/1/IMAGES-PREPARED` | Stage 2 completion marker |
| `build/default/builder-info.txt`, `build-metadata.json`, `build.log` | Pipeline records |
| `build/default/validation/<test-run>/` | Optional workstation acceptance reports |


`IMAGES-PREPARED` means the OS and bootloader are present, but both data partitions are **empty**. It is not the final ready-to-flash marker. Artifacts, build logs, and initialization metadata belong to the invoking user; ownership inside the OS and encrypted workspace images remains unchanged. Existing root-owned archives are not migrated or recursively chowned. Use a fresh user-writable `BUILD_DIR` for new runs. To report a build failure, include the final 100 lines of `build/default/build.log` and the complete error printed by the wrapper. The chroot stays inside BuildKit; failed-stage output is available in the streamed host build.log.

For configuration customization, pass extra `lb config` options after the build directory. Do not override kernel flavour, architecture or boot layout without updating the associated checks. For frozen package inputs and base image digest selection, see [reproducibility](#reproducibility).

## Initialize a pair

Run this in the host workstation shell:

```bash
make initialize PAIR=1 ACTIVE=1
# Enter/confirm the workspace encryption passphrase at ZFS's stdin prompt.
test -f build/default/pairs/1/READY
cat build/default/pairs/1/WORKSPACE-INFO.txt
```

This validates prepared metadata, refuses attached/previously initialized images, attaches both files using loop devices, verifies their data PARTUUIDs and blank signatures, and creates **one pool across both partitions**. Nothing is created on physical USBs at this stage.

The pool has `ashift=12`, `cachefile=none`, an unmountable root dataset, and an AES-256-GCM encrypted `workspace-1/data` with `keyformat=passphrase`, `keylocation=prompt`, `canmount=noauto`, LZ4 compression and the expected mountpoint. It briefly mounts the new dataset beneath a private temporary altroot to set the filesystem root to `2000:2000`, mode `0750`, without covering a host mountpoint. The passphrase is read directly by ZFS from stdin and is never saved in the image or manifest. The script exports the pool and detaches loops before writing `READY`. It never imports/exports unrelated host pools.

To support Ubuntu 22.04's OpenZFS 2.1 and Debian 13's newer ZFS, creation uses `zpool create -d` and enables **`lz4_compress`, `encryption` and encryption's required dependencies** (including `extensible_dataset` for resumable receives). These features are supported by OpenZFS 2.1. This omits newer optional pool features. `zpool status` may report that supported features are disabled; that message is expected. **Do not run `zpool upgrade`** if you need continued access from hosts using OpenZFS 2.1. Exact host/live-system package equality is not required.

If initialization fails, `INITIALIZING` remains and reruns are refused. Check `sudo zpool status workspace-1` and `sudo losetup -j /absolute/path/to/usb-a.img` (and B). Failed export retains loops rather than detaching an active pool. Resolve/export the pool and detach only this build's loops before removing incomplete artifacts. For an unused, failed initial build, reassemble from the ISO into a **new** output directory. Never discard images containing data you need.

## Reproducibility

Default Debian mirrors and the `debian:trixie` tag move over time. The wrapper records builder details and successful solve metadata in `builder-info.txt` and `build-metadata.json`; the chroot records package/kernel versions, and the host records its ZFS versions and pool GUID in `WORKSPACE-INFO.txt`.

ZFS is compiled with temporary target-kernel headers and DKMS, then installed as the independent `level1vault-zfs-modules` package. Compilers, headers, and DKMS are removed before the live filesystem is compressed. `/usr/local/share/level1vault/zfs-build-packages.tsv` records the compilation environment; `packages.tsv` records the shipped package set. Rebuild the whole live image for kernel/ZFS updates; the live OS does not rebuild modules at boot.

For repeatable package inputs, select verified Dockerfile frontend, BuildKit and Debian base image digests plus a Debian archive snapshot timestamp. Pin both the Dockerfile's toolchain package sources and live-build's bootstrap/chroot/security mirrors; pinning only the Docker image tag or only live-build mirrors is insufficient. The Dockerfile accepts `DEBIAN_IMAGE`; changing its APT sources to a historical snapshot is an explicit release configuration task.

Example live-build arguments (replace the timestamp and digest with verified values):

```bash
snapshot=REPLACE_WITH_VERIFIED_TIMESTAMP
env DEBIAN_IMAGE=debian:trixie@sha256:VERIFIED_DIGEST \
  ./pipeline/buildkit.sh build "$PWD/build-pinned" \
  --mirror-bootstrap "https://snapshot.debian.org/archive/debian/$snapshot/" \
  --mirror-chroot "https://snapshot.debian.org/archive/debian/$snapshot/" \
  --mirror-chroot-security "https://snapshot.debian.org/archive/debian-security/$snapshot/" \
  --apt-options '--yes -o Acquire::Check-Valid-Until=false'
```

Keep archive signature verification enabled. Expiry bypass is only for the selected historical signed snapshot. Verify trixie, trixie-updates and trixie-security are available. Record the Dockerfile and host ZFS environment with each release. New disk assembly deliberately generates new filesystem/GPT identities and host initialization creates a new pool GUID: raw images are not bit-identical reproducible outputs.
