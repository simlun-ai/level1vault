# level1vault

Any number of numbered pairs of independently bootable, stateless Debian 13 Live USBs: one active workspace mirror and encrypted replicas for offsite rotation. Build on an **amd64 Ubuntu 22.04 workstation with working ZFS**, using Debian Trixie tools inside Docker. The target boots through x86-64 UEFI with Secure Boot disabled.

Each default 12 GiB image has a GPT, a 4 GiB FAT32 ESP holding GRUB and the live payload, and an approximately 8 GiB data partition. SquashFS provides the read-only base; a nonpersistent RAM overlay holds OS changes. The `workspace-1/data` dataset mounts at `/mnt/workspace` only after `sudo workspace-mount 1` and passphrase entry. New workspaces use native AES-256-GCM encryption and belong to simlun (`2000:2000`, mode `0750`). Pairs have neutral numeric identities (`workspace-1`, `workspace-2`, …). After fencing a failed active pair, activate the next surviving ID in place. See the [N-pair runbook](docs/PAIRS.md) for provisioning, shared-checkpoint send/receive, rotation and ordered failover. This workflow provisions fresh encrypted pairs.

| Work | Where | Entry point |
| --- | --- | --- |
| Install build tools | Trixie Docker image | `docker/Dockerfile` |
| Build Debian kernel, initramfs, SquashFS and target ZFS DKMS module | Privileged Trixie build container | `build-container.sh`, `01-configure-live.sh` |
| Construct GPT images, FAT filesystems, per-drive UEFI loaders | Trixie container; file operations only | `02-assemble-images.sh` |
| Initialize one ZFS mirror across both image data partitions | Ubuntu host, using its existing ZFS stack and loop devices | `03-initialize-workspace.sh` |
| QEMU tests and physical USB writing | Ubuntu host | Commands below |

Docker shares the running host kernel. The container compiles ZFS **for Debian's target kernel**, with the Debian headers in the live-build chroot; it does not need to load that module. The host creates the pool using its own installed `zpool`/`zfs` and loaded module. Neither host kernel headers nor `/lib/modules` are bind-mounted into the container.

**State of validation:** [the full Debian Live build passed on Ubuntu 22.04](https://github.com/simlun-ai/level1vault/actions/runs/38636756155), including real DKMS compilation, initramfs generation and both 12 GiB boot images. The extracted SquashFS module matches kernel `6.12.111+deb13-amd64`; automatic import/mount disabling and final checksums also passed inspection. The operator verified section 4 in QEMU on the preceding image, including normal boot, workspace mounting and reboot persistence. Real Ubuntu 22.04 ZFS tests now cover encrypted initialization, UID/GID ownership, shared full/incremental checkpoints, interrupted-transfer resumption, read-only replica mounting, numeric in-place failover and safe source rebinding. The operator also confirmed real laptop hardware: each physical USB boots independently with Secure Boot disabled, and the ZFS mirror works with both connected. Degraded-write/rejoin tests remain unconfirmed. The numbered-pair activation/refactor in this revision still needs an operator boot/failover check. See [validation](docs/VALIDATION.md) and the [implementation review](docs/REVIEW.md).

## 1. Prepare the Ubuntu workstation

Use local, rootful Docker Engine on amd64 Linux. Docker Desktop, remote Docker daemons, rootless Docker and cross-architecture builds are not supported by the wrapper. The first full CI build took about 17 minutes on its runner; workstation time varies. Have at least 40 GiB of free working space; more is useful for keeping multiple build versions. Use a native Linux filesystem for the build directory. From an existing checkout, run `git switch main` and `git pull --ff-only` before the first build.

If Docker is not installed, Ubuntu's package is sufficient for this workflow:

```bash
sudo apt update
sudo apt install -y docker.io git gdisk parted util-linux udev kmod python3 \
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

## 2. Build the live system and boot images in Docker

```bash
git clone --branch main https://github.com/simlun-ai/level1vault.git
cd level1vault
sudo ./build-container.sh "$PWD/build"
```

The workflow is available on `main`. The wrapper builds `level1vault-builder:trixie`, then runs live-build and file-only disk assembly. It refuses an existing build directory, preventing accidental reuse of stale chroots/configuration. A later build can use `"$PWD/build-next"` instead.

The live image includes one Debian amd64 kernel and matching headers. Its chroot hook runs `dkms autoinstall -k <target-version>`, `depmod` and `modinfo` for that kernel, then regenerates initramfs. No running-host `uname -r` is used to select the target kernel. `zfs-initramfs` is excluded because the data pool must not be a boot dependency. Import/mount services and the ZFS systemd mount generator are disabled.

Assembly uses `xorriso` to extract the ISO, `sgdisk` to partition regular files, `mkfs.vfat`/mtools to populate separate FAT images, and `grub-mkstandalone` to generate each fallback EFI loader. It embeds each FAT image at partition 1's offset. No assembly-time loop devices, udev daemon, mounts, or ZFS are needed.

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

For configuration customization, pass extra `lb config` options after the build directory. Do not override kernel flavour, architecture or boot layout without updating the associated checks. For frozen package inputs and base image digest selection, see [reproducibility](docs/REVIEW.md#reproducibility).

## 3. Initialize the mirror on the Ubuntu host

Exit any build container; run this in the Ubuntu workstation shell:

```bash
sudo ./03-initialize-workspace.sh "$PWD/build/out" 1 --active
# Enter/confirm the workspace encryption passphrase at ZFS's stdin prompt.
test -f build/out/READY
cat build/out/WORKSPACE-INFO.txt
(cd build/out && sha256sum -c SHA256SUMS)
```

This verifies the prepared checksums, refuses attached/previously initialized images, attaches both files using loop devices, verifies their data PARTUUIDs and blank signatures, and creates **one pool across both partitions**. Nothing is created on physical USBs at this stage.

The pool has `ashift=12`, `cachefile=none`, an unmountable root dataset, and an AES-256-GCM encrypted `workspace-1/data` with `keyformat=passphrase`, `keylocation=prompt`, `canmount=noauto`, LZ4 compression and the expected mountpoint. It briefly mounts the new dataset beneath a private temporary altroot to set the filesystem root to `2000:2000`, mode `0750`, without covering a host mountpoint. The passphrase is read directly by ZFS from stdin and is never saved in the image or manifest. The script exports the pool and detaches loops before writing `READY` and final image checksums. It never imports/exports unrelated host pools.

To support Ubuntu 22.04's OpenZFS 2.1 and Debian 13's newer ZFS, creation uses `zpool create -d` and enables **`lz4_compress`, `encryption` and encryption's required dependencies** (including `extensible_dataset` for resumable receives). These features are supported by OpenZFS 2.1. This omits newer optional pool features. `zpool status` may report that supported features are disabled; that message is expected. **Do not run `zpool upgrade`** if you need continued Ubuntu 22.04 access. Exact Ubuntu/Debian package equality is no longer required.

If initialization fails, `INITIALIZING` remains and reruns are refused. Check `sudo zpool status workspace-1` and `sudo losetup -j /absolute/path/to/usb-a.img` (and B). Failed export retains loops rather than detaching an active pool. Resolve/export the pool and detach only this build's loops before removing incomplete artifacts. For an unused, failed initial build, reassemble from the ISO into a **new** output directory. Never discard images containing data you need.

## 4. Test in QEMU on Ubuntu

Never attach the images to QEMU while the host has their pool imported or their loop mappings active. Keep only one active owner of this mirror. From `build/out/`:

```bash
sudo cp /usr/share/OVMF/OVMF_VARS_4M.fd ./ovmf-test-vars.fd
sudo chown "$(id -u):$(id -g)" usb-a.img usb-b.img ovmf-test-vars.fd
qemu-system-x86_64 \
  -machine q35,accel=kvm -cpu host -m 4096 -smp 2 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file=./ovmf-test-vars.fd \
  -drive if=virtio,format=raw,file=./usb-a.img \
  -drive if=virtio,format=raw,file=./usb-b.img
```

On Ubuntu 22.04 installations that supply `OVMF_CODE.fd` and `OVMF_VARS.fd` instead of the `_4M` files, replace both paths with that matching pair. Check `ls /usr/share/OVMF` first. Run QEMU as your desktop user, without sudo, to avoid GTK authorization errors. KVM access was already available on the tested workstation; group changes are only necessary if /dev/kvm access is denied. Use `accel=tcg` and omit `-cpu host` if KVM is unavailable. Secure Boot must be disabled. The live console autologs in as `simlun` (UID 2000, primary GID 2000), with password `password` and passwordless sudo. `/usr/local/sbin` is included in the login PATH and sudo secure_path. The login MOTD displays `Hello, world` from `/etc/motd`. No SSH server or remote authentication is configured.

Inside the guest:

```bash
cat /proc/cmdline
findmnt -t overlay,squashfs,tmpfs
sudo modprobe zfs
sudo zpool list                    # workspace should not be imported yet
sudo workspace-mount 1              # enter the workspace passphrase
stat -c '%u:%g %a' /mnt/workspace # 2000:2000 750
sudo zpool status workspace-1        # ONLINE with both members present
printf 'persistent\n' > /mnt/workspace/check.txt # writable as simlun
printf 'ephemeral\n' | sudo tee /etc/reset-check
cd /
sudo workspace-umount
sudo reboot
```

After reboot `/etc/reset-check` must be absent; manually mount the workspace and confirm `check.txt` survives. Run `cd /` then `sudo workspace-umount` before shutting down. Also boot **A alone and B alone**, using fresh OVMF variables, and perform the degraded/rejoin tests in [VALIDATION.md](docs/VALIDATION.md). Virtio tests do not replace USB transport/hardware tests.

VM writes change the raw image hashes. After completing tests, with the pool exported, VM stopped and loops detached, record new hashes:

```bash
(cd build/out && sha256sum usb-a.img usb-b.img images.json | sudo tee SHA256SUMS)
```

## 5. Write physical USB drives on Ubuntu

Use two drives of at least 12 GiB each (16 GiB or larger recommended). Disconnect any loop-mounted image copies and other flashed copies of the same pair: GPT/PARTUUID/ZFS identities are duplicated by flashing.

Identify the **whole disks** by model, serial and size:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
```

Only after verifying that `/dev/sdb` and `/dev/sdc` are the intended USB drives, run from the repository root:

```bash
test -f build/out/READY
(cd build/out && sha256sum -c SHA256SUMS)
# DESTRUCTIVE: replace these example device names with verified whole USB disks.
# Unmount every mounted partition on the selected USBs first; export any pools on them.
sudo dd if=build/out/usb-a.img of=/dev/sdb bs=4M conv=fsync status=progress
sudo dd if=build/out/usb-b.img of=/dev/sdc bs=4M conv=fsync status=progress
sync
# The image's backup GPT initially lies before the end of a larger real disk.
sudo sgdisk -e /dev/sdb
sudo sgdisk -e /dev/sdc
sudo partprobe /dev/sdb /dev/sdc
```

The data partition remains approximately 8 GiB; unused physical capacity stays unallocated. Relocating the backup GPT does not resize either partition. Verify each drive boots alone before relying on the pair.

## 6. Operation and recovery

`workspace-umount` takes the same lock as the mount helper, syncs and exports all imported numbered workspace pools without forcing. With a numeric pair ID it selects only that pair; the default selects all imported numbered pairs. It succeeds harmlessly when already exported. A busy/failed export exits nonzero; close applications and leave the workspace in every shell before retrying. It never powers off the machine itself.

```bash
sudo workspace-mount 1
# Before shutdown, removing drives or changing hosts:
cd /
sudo workspace-umount
sudo poweroff
```

The mount helper serializes calls, creates a per-boot hostid if absent, imports without mounting by scanning PARTUUIDs, validates the pair and encryption properties, loads the key through stdin if needed, then explicitly mounts only the selected data filesystem. `workspace-mount PAIR_ID` selects that pair and uses its active/replica state; only one dataset can use `/mnt/workspace` at a time. It does not force imports. It may import a valid single member in degraded mode. A random hostid means an unclean shutdown may require a deliberate, fenced force import on the next boot.

Never write independently to separated members and expect a merge. After writes to A while B was missing, B is stale; reconnect to the authoritative pool owner, resilver, and confirm **both members ONLINE with no errors** before using B independently. `zpool wait -t resilver workspace-1` alone is insufficient: it also returns when no resilver is running.

If import reports a potentially active pool, establish that no other machine/VM owns either member, then inspect the pool GUID from `WORKSPACE-INFO.txt` and the import listing. Only after resolving ownership/history may you manually use:

```bash
sudo modprobe zfs
[ -s /etc/hostid ] || sudo zgenhostid
sudo zpool import -d /dev/disk/by-partuuid
sudo zpool import -f -N -d /dev/disk/by-partuuid -o cachefile=none VERIFIED_POOL_GUID
sudo workspace-mount 1
```

For additional pairs, periodic `workspace-sync SOURCE_ID TARGET_ID...`, snapshot retention and ordered in-place failover, use [PAIRS.md](docs/PAIRS.md).

For online/replacement procedures and divergent-history restrictions, see [REVIEW.md](docs/REVIEW.md#recovery-details). Never run `zpool create` on a surviving member, `zpool add` to replace a mirror member, or `zpool upgrade` just to dismiss the expected feature warning. Do not unplug the running boot medium: SquashFS is still read from it unless explicitly copied to RAM.

## 7. OS updates and limitations

**Do not reflash complete images to update an existing pair with valuable data.** Whole-image writes overwrite the workspace with an old pool member. Rebuild the ISO in Docker, then on the host replace only each ESP's live payload, preserving that ESP's FAT UUID, GRUB configuration, partition table and data partition. Copy kernel/initrd as regular files with `cp -L`; use FAT-compatible copying (`rsync -rt`, not `-a`), retaining any `.disk` UUID markers from the new ISO. Back up the old payload and update/test one USB before the other. Such copies are not atomic.

- The running root resets; FAT/SquashFS stored on USB are not physically write-protected or cryptographically verified.
- Live changes and the ZFS ARC consume RAM; test the minimum hardware configuration.
- Prefer reliable USB SSDs over low-end flash sticks and keep independent backups.
- Each assembled pair has its own identities. Initialized images are not generic replacement-member templates.
- Signing EFI/kernel/DKMS for Secure Boot, remote authentication and automated replacement-drive provisioning remain outside this workflow.

## References

- [Debian live-build](https://manpages.debian.org/trixie/live-build/lb_config.1.en.html)
- [Debian live-boot](https://manpages.debian.org/trixie/live-boot-doc/live-boot.7.en.html)
- [DKMS target kernel selection](https://manpages.debian.org/trixie/dkms/dkms.8.en.html)
- [Docker run and privileges](https://docs.docker.com/engine/containers/run/)
- [sgdisk](https://manpages.debian.org/trixie/gdisk/sgdisk.8.en.html), [mcopy](https://manpages.debian.org/trixie/mtools/mcopy.1.en.html)
- [OpenZFS pool creation](https://manpages.debian.org/trixie/zfsutils-linux/zpool-create.8.en.html) and [feature compatibility](https://manpages.debian.org/trixie/zfsutils-linux/zpool-features.7.en.html)
