# Implementation review — 2026-10-14

Updated for the Trixie-container / Ubuntu-host build split. The original draft and initial all-Debian implementation remain in Git history.

Scope: the build wrappers, live configuration, runtime helper, host initializer, CI/tests and operator documentation. The original implementation is preserved in Git history. This review corrects source/configuration and command handling; it does not establish that a complete image boots on hardware.

## Findings and changes

| Area | Finding in supplied draft | Resolution / remaining check |
| --- | --- | --- |
| live-build kernel packages | `linux-image linux-headers` with flavour amd64 is valid. | Retained; hook now rejects zero or multiple bootable kernels. |
| live-build environment | Host defaults and recommended packages affect the image implicitly. | Ignore system defaults, disable recommendations, explicitly include live-config-systemd, a fixed simlun account hook, initramfs backend, DKMS build dependencies and console tools. |
| headless access | No SSH server or remote authentication configuration; the local live account is explicitly provisioned. | Local console bring-up is explicit; remote access is not promised. Networking on actual NICs still needs testing. |
| DKMS | Presence check is useful, but an empty kernel loop could succeed. Existing initramfs was not refreshed after identity/cache cleanup. | Exactly one kernel required; matching headers/DKMS/depmod/modinfo; regenerate initramfs afterward. Guest modprobe is required to validate loadability. |
| ZFS boot dependency | Data pool must not be needed for boot. | Exclude zfs-initramfs; reject unexpected installation. Compile ZFS into SquashFS, not at each boot. |
| automatic mounts | Masking zfs-mount.service alone does not cover mount-generator units. `canmount=on` allows broad mounts. | Mask generator, clear caches, mask import services; create data with noauto and generator-ignore property. |
| FAT32 assembly | `cp -a` copies Unix ownership/modes and can attempt symlinks; FAT does not support them. | Copy selected kernel/initrd with dereferencing and no Unix metadata. Enforce per-file limit and a size budget; mtools rejects an overflowing ESP. |
| ISO payload | Assumes stable kernel aliases and omits optional medium UUID markers. | Prefer manifest-selected versioned files, accept aliases, retain `.disk` markers. Single kernel is a build invariant. |
| Container boundary | Docker shares the host kernel; Debian zpool in a container would talk to Ubuntu's loaded module. | Compile target DKMS with Debian headers in the chroot; create the pool with host tools. Container assembly uses only regular files, sgdisk and mtools. |
| EFI loader | grub-install is reasonable on normal systems, but introduces host filesystem/disk probing when assembling loop images. | Standalone EFI image with an embedded search for that ESP UUID; load external menu. No NVRAM writes. Firmware validation remains required. |
| live-media | Parameter is a preferred device, not strict fencing to it. | Document fallback semantics; boot each image alone to prove independence. |
| pool format | Pool is created using the host OpenZFS version; default feature set may be newer than live userland. | Host creates with -d and enables lz4_compress plus encryption and its required dependencies, a shared baseline for Ubuntu 22.04 OpenZFS 2.1 and Debian 13. Package version equality is not required. Do not upgrade features. |
| cleanup | Draft detaches loop devices even after a failed pool export. | Host initializer preserves loops after failed export or uncertain pool-creation state. INITIALIZING blocks reruns after failure; READY appears only after successful export/detach and final hashes. Assembly uses no mounts/loops. |
| mount helper | Could report success with a changed dataset mountpoint, obscure a nonempty directory, or race another invocation. | Lock; validate expected properties/mount source; reject nonempty mountpoint or dataset mounted elsewhere. Import has no force option. |
| boot identity | Per-boot random hostid is valid for clean exports; unclean shutdown can cause foreign-host refusal. | Retained deliberately, documented manual fenced force import by numeric pool GUID. It is not a multi-host coordination mechanism. |
| resilver | Wait command can return when no recovery is running, including a missing device. | Require final status, both members ONLINE and error inspection; provide online/replace procedures. |
| flashing | GPT backup initially remains at the raw image end on larger sticks. Clones duplicate IDs. | Relocate backup GPT with sgdisk -e; document no original loops / duplicate physical members on same host. |
| OS updates | Original rsync -a repeats FAT metadata issue; copy is not atomic. | Stage regular files, use rsync -rt, preserve UUID markers, update one boot environment at a time with backup. |
| reproducibility | Rolling mirrors, unrecorded packages and randomized identities. | Track source, Docker image identity, package manifests, prepared/final checksums and pool identifiers. Snapshot pinning remains an operator-selected input. No claim of bit-for-bit disk reproduction. |
| mirror architecture | One pool across both partitions is correct. Separate writable branches cannot be merged. | Preserve single-owner rule, explicitly test degraded write followed by authoritative-member resilver. |

## Encrypted numbered-pair extension — 2026-10-15

New active datasets use AES-256-GCM/passphrase encryption and root ownership 2000:2000. Any number of pairs use neutral numeric identities (`workspace-1`, `workspace-2`, …), with active/replica state stored independently. Replication sends one shared raw checkpoint to explicitly selected destinations; interrupted receives keep independent held bases. Fenced activation selects the next numeric ID in place and makes its existing data writable. A source rebind after failover requires a completed common latest destination snapshot; no force receive, rollback or snapshot deletion is used. See [pair operations](PAIRS.md) and [validation](VALIDATION.md).

The operator confirmed independent boot of both real USB sticks on a laptop with Secure Boot disabled, plus a working ZFS mirror with both sticks connected. Physical degraded-write/rejoin and the newly introduced numbered failover workflow remain acceptance steps.

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

## Recovery details

Before every recovery: stop all other pool owners, disconnect duplicate image/physical copies, retain backups, identify authoritative data. Preserve the original build manifest outside the pool.

For a formerly disconnected **original member**, reattach both devices on the authoritative host and import with the mount helper. Inspect:

```bash
sudo zpool status -gP workspace-1
lsblk -o PATH,SIZE,FSTYPE,PARTUUID,UUID,MODEL,SERIAL
ls -l /dev/disk/by-partuuid/
```

If a known member remains unavailable, identify its numeric vdev GUID in status and its matching PARTUUID from images.json. After confirming they are the same original member, try bringing it online:

```bash
sudo zpool online workspace-1 EXISTING_MEMBER_GUID
sudo zpool wait -t resilver workspace-1
sudo zpool status -gP workspace-1
```

If its path cannot be resolved, do not blindly replace it with a stale copy. Inspect labels with `sudo zdb -l /dev/disk/by-partuuid/VERIFIED_PARTUUID`; zdb is supplied by zfsutils-linux. A normal replacement is for a genuinely failed/lost device, not an attempt to reconcile independently written members.

For a **new replacement USB**, first prepare its boot environment without creating a new pool or overwriting the survivor. Use the assembly script's file-based partition/ESP/standalone-GRUB steps as the design reference, but assign fresh GPT/FAT IDs and its own embedded/menu UUID. There is intentionally no automated replacement builder yet. The replacement ZFS partition must be at least the old member's size. Once it has a new, empty, verified partition:

```bash
# MUTATES the selected replacement partition; double-check serial and PARTUUID.
sudo zpool replace workspace-1 FAILED_MEMBER_GUID /dev/disk/by-partuuid/NEW_PARTUUID
sudo zpool wait -t resilver workspace-1
sudo zpool status -gP workspace-1
```

Do not use `zpool create`, do not dd an initialized old workspace image over a new member, and do not add a top-level vdev (`zpool add`). Require both ONLINE and a clean final status before independent use. Save the new member's identities and test its boot environment alone.

When only inspecting a potentially stale original member, first fence every other owner and import by GUID with readonly=on; do not run the normal read-write mount helper on the stale member. Never add `-F`, `-X` or `-T` rewind/recovery flags as a routine fix.

## Documentation checked

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
