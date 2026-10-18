# Architecture

Any number of numbered pairs of independently bootable, stateless Debian 13 Live USBs: one active workspace mirror and encrypted replicas for offsite rotation. Build on an **Linux amd64 workstation with working ZFS**, using Debian Trixie tools inside Docker. The target boots through x86-64 UEFI with Secure Boot disabled.

Each default 12 GiB image has a GPT, a 4 GiB FAT32 ESP holding GRUB and the live payload, and an approximately 8 GiB data partition. SquashFS provides the read-only base; a nonpersistent RAM overlay holds OS changes. The `workspace-1/data` dataset mounts at `/mnt/workspace` only after `sudo workspace-mount 1` and passphrase entry. New workspaces use native AES-256-GCM encryption and belong to simlun (`1000:1000`, mode `0750`). Pairs have neutral numeric identities (`workspace-1`, `workspace-2`, …). After fencing a failed active pair, activate the next surviving ID in place. See the [N-pair runbook](PAIRS.md) for provisioning, shared-checkpoint send/receive, rotation and ordered failover. This workflow provisions fresh encrypted pairs.

The live console autologs in as root. `start` explicitly unlocks the active workspace, bind-mounts its `home/simlun` directory at `/home/simlun`, and enters a simlun login environment in tmux. Home data shares the encrypted dataset and replication history; root and OS changes stay temporary. See [home and session lifecycle](HOME.md).

| Work | Where | Entry point |
| --- | --- | --- |
| Install build tools | Trixie Docker image | `pipeline/Dockerfile` toolchain stage |
| Build Debian kernel, initramfs, SquashFS and target ZFS DKMS module | Privileged BuildKit live stage | `pipeline/buildkit.sh build`, `stages/01-build-live/configure.sh` |
| Construct GPT images, FAT filesystems, per-drive UEFI loaders | Sandboxed BuildKit assembly stage; file operations only | `stages/02-assemble-pair/assemble.sh` |
| Initialize one ZFS mirror across both image data partitions | Linux host, using its existing ZFS stack and loop devices | `stages/03-initialize-pair/initialize.sh` |
| QEMU tests and physical USB writing | Linux host | [QEMU](QEMU.md) and [USB writing](../stages/05-write-usb/README.md) |

Docker shares the running host kernel. The container compiles ZFS **for Debian's target kernel**, with the Debian headers in the live-build chroot; it does not need to load that module. The host creates the pool using its own installed `zpool`/`zfs` and loaded module. Neither host kernel headers nor `/lib/modules` are bind-mounted into the container.


## Live system and disk layout

The live image includes one Debian amd64 kernel and a matching `level1vault-zfs-modules` Debian package. Its chroot hook temporarily installs headers, compilers, and DKMS, runs `dkms autoinstall -k <target-version>`, and packages the complete compiled module set under `/usr/lib/modules/<target-version>/extra/level1vault/`. The standalone package depends on the exact target kernel package and ZFS userland version and provides `zfs-modules`. The hook then purges the temporary build packages, verifies module ownership, vermagic, and dependency resolution, and regenerates initramfs. The shipped OS has no compiler, headers, or DKMS. No running-host `uname -r` is used to select the target kernel. `zfs-initramfs` is excluded because the data pool must not be a boot dependency. Import/mount services and the ZFS systemd mount generator are disabled.

Assembly uses `xorriso` to extract the ISO, `sgdisk` to partition regular files, `mkfs.vfat`/mtools to populate separate FAT images, and `grub-mkstandalone` to generate each fallback EFI loader. It embeds each FAT image at partition 1's offset. No assembly-time loop devices, udev daemon, mounts, or ZFS are needed.

Live-build selects only `grub-efi`; the build toolchain does not install Syslinux/ISOLINUX. Legacy memtest and the Windows loader are disabled. The intermediate ISO keeps its existing filename for stage compatibility; its El Torito catalog must contain EFI support and no BIOS boot entry. Physical USBs use stage 2's standalone x86_64 EFI loaders and require UEFI firmware with Secure Boot disabled.

## Design review

| Area | Finding in supplied draft | Resolution / remaining check |
| --- | --- | --- |
| live-build kernel packages | `linux-image linux-headers` with flavour amd64 is valid. | Only `linux-image` is permanent; matching headers are temporary build dependencies. The hook rejects zero or multiple bootable kernels. |
| live-build environment | Host defaults and recommended packages affect the image implicitly. | Ignore system defaults, disable recommendations, explicitly include live-config-systemd, a fixed simlun account hook, initramfs backend, DKMS build dependencies and console tools. |
| headless access | No SSH server or remote authentication configuration; the local live account is explicitly provisioned. | Local console bring-up is explicit; remote access is not promised. Networking on actual NICs still needs testing. |
| DKMS | Presence check is useful, but an empty kernel loop could succeed. Existing initramfs was not refreshed after identity/cache cleanup. | Exactly one kernel required; matching headers/DKMS/depmod/modinfo; regenerate initramfs afterward. Guest modprobe is required to validate loadability. |
| ZFS boot dependency | Data pool must not be needed for boot. | Exclude zfs-initramfs; reject unexpected installation. Compile ZFS into SquashFS, not at each boot. |
| automatic mounts | Masking zfs-mount.service alone does not cover mount-generator units. `canmount=on` allows broad mounts. | Mask generator, clear caches, mask import services; create data with noauto and generator-ignore property. |
| FAT32 assembly | `cp -a` copies Unix ownership/modes and can attempt symlinks; FAT does not support them. | Copy selected kernel/initrd with dereferencing and no Unix metadata. Enforce per-file limit and a size budget; mtools rejects an overflowing ESP. |
| ISO payload | Assumes stable kernel aliases and omits optional medium UUID markers. | Prefer manifest-selected versioned files, accept aliases, retain `.disk` markers. Single kernel is a build invariant. |
| Container boundary | Docker shares the host kernel; Debian zpool in a container would talk to the host's loaded module. | Compile target DKMS with Debian headers in the chroot; create the pool with host tools. Container assembly uses only regular files, sgdisk and mtools. |
| EFI loader | grub-install is reasonable on normal systems, but introduces host filesystem/disk probing when assembling loop images. | Standalone EFI image with an embedded search for that ESP UUID; load external menu. No NVRAM writes. Firmware validation remains required. |
| live-media | Parameter is a preferred device, not strict fencing to it. | Document fallback semantics; boot each image alone to prove independence. |
| pool format | Pool is created using the host OpenZFS version; default feature set may be newer than live userland. | Host creates with -d and enables lz4_compress plus encryption and its required dependencies, a shared baseline for Ubuntu 22.04 OpenZFS 2.1 and Debian 13. Package version equality is not required. Do not upgrade features. |
| cleanup | Draft detaches loop devices even after a failed pool export. | Host initializer preserves loops after failed export or uncertain pool-creation state. INITIALIZING blocks reruns after failure; READY appears only after successful export/detach. Assembly uses no mounts/loops. |
| mount helper | Could report success with a changed dataset mountpoint, obscure a nonempty directory, or race another invocation. | Lock; validate expected properties/mount source; reject nonempty mountpoint or dataset mounted elsewhere. Import has no force option. |
| boot identity | Per-boot random hostid is valid for clean exports; unclean shutdown can cause foreign-host refusal. | Retained deliberately, documented manual fenced force import by numeric pool GUID. It is not a multi-host coordination mechanism. |
| resilver | Wait command can return when no recovery is running, including a missing device. | Require final status, both members ONLINE and error inspection; provide online/replace procedures. |
| flashing | GPT backup initially remains at the raw image end on larger sticks. Clones duplicate IDs. | Relocate backup GPT with sgdisk -e; document no original loops / duplicate physical members on same host. |
| OS updates | Original rsync -a repeats FAT metadata issue; copy is not atomic. | Stage regular files, use rsync -rt, preserve UUID markers, update one boot environment at a time with backup. |
| reproducibility | Rolling mirrors, unrecorded packages and randomized identities. | Track source, Docker image identity, package manifests and pool identifiers. Snapshot pinning remains an operator-selected input. No claim of bit-for-bit disk reproduction. |
| mirror architecture | One pool across both partitions is correct. Separate writable branches cannot be merged. | Preserve single-owner rule, explicitly test degraded write followed by authoritative-member resilver. |

## References



- [Debian 13 lb_config](https://manpages.debian.org/trixie/live-build/lb_config.1.en.html)
- [Debian 13 live-boot](https://manpages.debian.org/trixie/live-boot-doc/live-boot.7.en.html)
- [Debian 13 live-config](https://manpages.debian.org/trixie/live-config-doc/live-config.7.en.html)
- [OpenZFS Debian installation](https://openzfs.github.io/openzfs-docs/Getting%20Started/Debian/index.html)
- [GRUB standalone](https://manpages.debian.org/trixie/grub-common/grub-mkstandalone.1.en.html)
- [ZFS properties](https://manpages.debian.org/trixie/zfsutils-linux/zfsprops.7.en.html)
- [ZFS mount generator](https://manpages.debian.org/trixie/zfsutils-linux/zfs-mount-generator.8.en.html)
- [Pool create](https://manpages.debian.org/trixie/zfsutils-linux/zpool-create.8.en.html), [import](https://manpages.debian.org/trixie/zfsutils-linux/zpool-import.8.en.html), [export](https://manpages.debian.org/trixie/zfsutils-linux/zpool-export.8.en.html)
- [Online](https://manpages.debian.org/trixie/zfsutils-linux/zpool-online.8.en.html), [replace](https://manpages.debian.org/trixie/zfsutils-linux/zpool-replace.8.en.html), [wait](https://manpages.debian.org/trixie/zfsutils-linux/zpool-wait.8.en.html)
- [Pool feature compatibility](https://manpages.debian.org/trixie/zfsutils-linux/zpool-features.7.en.html)

The current [BuildKit pipeline](BUILDKIT.md) exports artifacts from scratch stages. The table of design findings above records the original review; host/container ZFS separation still applies.

