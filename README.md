# level1vault

Two independently bootable Debian 13 Live USB images with one shared ZFS mirror.

See [the implementation review](docs/REVIEW.md) for findings and [the acceptance checklist](docs/VALIDATION.md) for required VM/hardware checks. The supplied draft is preserved in Git history. The current code has passed syntax and mocked behavior checks, **not a full Debian build or boot test**.

**Target:** Debian 13 Trixie amd64, x86-64 UEFI, Secure Boot **disabled**. Run on a disposable Debian 13 build VM. The two sparse outputs are 12 GiB each: approximately 4 GiB ESP/FAT32 containing GRUB and the live filesystem, and approximately 8 GiB ZFS workspace partition. Requires two USB drives of at least 12 GiB each (16 GiB or larger recommended).

**Goal:** both USB sticks are individually bootable; the Debian root is a non-persistent live OS; the writable ZFS workspace is a two-way mirror when both drives are present. The dataset mounts **only** when `/usr/local/sbin/mount-workspace` is called.

**Crucial limitation:** ZFS mirroring requires a single active pool owner. Never mount/read-write each separated USB on different hosts and then try to merge their changes; that is not supported. A disconnected, formerly writable mirror member is stale and must resilver before being booted and written independently.

## 0. Host preparation

Use Debian 13 amd64 in a VM or disposable workstation. Have at least 40 GiB free and enough space for the live-build chroot and caches. The system running the build must have a loadable ZFS module; a container without `/lib/modules` support is not sufficient. Secure Boot should be disabled on the target UEFI computers; these directions do not sign the DKMS module or EFI loader.

Enable Debian `contrib` in your apt sources. On a default Debian 13 deb822 apt layout:

```bash
sudo sed -i -E '/^Components:/ { /(^|[[:space:]])contrib([[:space:]]|$)/! s/$/ contrib/; }' /etc/apt/sources.list.d/debian.sources
sudo apt update
sudo apt install -y \
    live-build debootstrap xorriso squashfs-tools \
    dosfstools mtools isolinux syslinux-common \
    grub-efi-amd64-bin grub2-common grub-common \
    parted gdisk util-linux rsync qemu-system-x86 ovmf \
    linux-image-amd64 linux-headers-amd64 build-essential dkms zfs-dkms zfsutils-linux git python3 shellcheck
sudo apt install -y "linux-headers-$(uname -r)"
sudo modprobe zfs
zpool --version
```

If your VM uses a traditional `sources.list`, enable the `contrib` component there instead; run `apt update` before installing. The build is architecture-specific (`amd64`). If the running kernel has no headers available, update the kernel and reboot the build VM before proceeding. If `modprobe zfs` fails, repair the host's DKMS/kernel header installation before continuing. `zfs-dkms` is sourced from Debian `contrib`.

## 1. Build the Debian Live ISO

Clone the repository, then configure in an isolated build directory:

```bash
git clone https://github.com/simlun-ai/level1vault.git
cd level1vault
python3 -m unittest discover -s tests -v
shellcheck 01-configure-live.sh 02-assemble-images.sh live/mount-workspace live/90-zfs.hook.chroot
mkdir build
cd build
../01-configure-live.sh
set -o pipefail
sudo lb build 2>&1 | tee build.log
```

`01-configure-live.sh` installs version-controlled sources from `live/` into the generated configuration. It ignores host live-build defaults, explicitly selects SquashFS/live-boot/systemd, and includes the systemd live-config backend and user-setup even with package recommendations disabled. No desktop task or SSH server is installed. Use a local text console for first bring-up; remote login/authentication remains a separate configuration task.

`--linux-packages 'linux-image linux-headers'` is valid: live-build appends the `amd64` flavour to each package name. The hook requires exactly one bootable kernel, builds and checks its ZFS module, masks automatic import/mount services and the ZFS mount generator, removes hostid/pool caches, then regenerates initramfs. ZFS is not the root filesystem; **zfs-initramfs is deliberately excluded**. A ZFS failure must not prevent a USB from booting.

The hook records the kernel and all installed package versions under `/usr/local/share/level1vault/`. Keep `build.log`, the ISO, package manifest and output build manifest for every release. A DKMS `modinfo` check proves a module is present, not that it loads successfully; guest `modprobe zfs` is still mandatory.

If the image build succeeds:

```bash
ls -lh live-image-amd64.hybrid.iso
```

Review any DKMS failures carefully; do not move on unless it succeeded. The produced ISO is an *intermediate build artifact*; the two final images will have their own GPT partition layouts and UEFI loaders.

### Inspect the live filesystem, optional but useful

```bash
mkdir -p /tmp/live-iso
sudo mount -o loop,ro live-image-amd64.hybrid.iso /tmp/live-iso
ls -lah /tmp/live-iso/live/
sudo umount /tmp/live-iso
```

At a minimum, there must be a `vmlinuz`, an `initrd.img`, and a `filesystem.squashfs` in the `/live` directory. The assembly script enforces this.

## 2. Construct both USB images and the ZFS mirror

From the repository root (leave the `build/` directory first):

```bash
sudo ./02-assemble-images.sh "$PWD/build/live-image-amd64.hybrid.iso" "$PWD/out"
```

The script performs all of these operations, rather than leaving partitioning to you:

1. Refuses an existing output directory, verifies a Debian 13 amd64 host, and requires matching live/host ZFS package versions and matching loaded-module/userland versions.
2. Creates two sparse 12 GiB raw disk images.
3. Attaches them as distinct loop devices and creates **unique GPTs**.
4. Creates partition 1 as a 4 GiB FAT32 ESP (`LIVE_A` or `LIVE_B`), and partition 2 as the remaining ZFS member.
5. Copies the selected kernel/initrd as regular files, SquashFS, the package manifest and any `.disk` UUID markers; checks FAT32 file size and free space. Unix attributes and symlinks are not copied to FAT.
6. Builds a standalone UEFI GRUB loader in `EFI/BOOT/BOOTX64.EFI`. Its embedded config selects that ESP by UUID and loads its external menu; no host disk probing or NVRAM changes are involved.
7. Generates each GRUB menu to select its own FAT volume using its unique filesystem UUID. It also passes `boot=live components nopersistence` and `live-media=` to Debian live-boot.
8. Creates **one pool**, `workspace`, mirrored over both loop partitions, with `ashift=12` and `cachefile=none`.
9. Creates **one dataset**, `workspace/data`, with `/mnt/workspace`, `canmount=noauto`, LZ4 compression and `org.openzfs.systemd:ignore=on`. Creation, import and `zfs mount -a` must not mount it.
10. Records a build manifest with pool GUID/PARTUUIDs, exports the pool, unmounts and detaches loops. Failed export/unmount retains the loops for investigation; a failed build never retains a `READY` marker.

```bash
test -f out/READY
ls -lh out/usb-a.img out/usb-b.img
cat out/BUILD-INFO.txt
sudo sfdisk -l out/usb-a.img
sudo sfdisk -l out/usb-b.img
sha256sum -c out/SHA256SUMS
```

Note: each 12 GiB image contains its own bootable OS plus a **mirror member**, not a standalone independently initialized ZFS pool. Never run `zpool create` separately for the second image.

## 3. Test both images in QEMU before flashing

Run from `out/`. Never attach these images to QEMU while their loop devices or ZFS pool are active on the build host. Confirm `sudo zpool list` does not contain workspace and `sudo losetup -j "$PWD/usb-a.img"` / `usb-b.img` return no mappings. The QEMU example attaches disks as virtio; test USB transport too before hardware rollout (see docs/VALIDATION.md).

Use OVMF to test **UEFI** boot; ordinary SeaBIOS mode does not exercise this image. The following paths are typical for Debian 13 (`ls /usr/share/OVMF/` if your distro provides differently named variants):

```bash
cp /usr/share/OVMF/OVMF_VARS_4M.fd ./ovmf-test-vars.fd
qemu-system-x86_64 \
  -machine q35,accel=kvm \
  -m 4096 -smp 2 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file=./ovmf-test-vars.fd \
  -drive if=virtio,format=raw,file=./usb-a.img \
  -drive if=virtio,format=raw,file=./usb-b.img
```

If KVM isn't available, use `-machine q35,accel=tcg` (slower). The default Debian Live console user is normally `user`, and Debian live user-setup normally uses the password `live`; `live-config-systemd` configures console autologin and sudo access. Verify these defaults in the guest before relying on unattended operation.

Inside the booted live guest:

```bash
cat /proc/cmdline
uname -r
sudo modprobe zfs
modinfo zfs | head
sudo zpool import -d /dev/disk/by-partuuid
sudo zpool list                   # workspace must not already be imported
findmnt /mnt/workspace || true
sudo /usr/local/sbin/mount-workspace
findmnt /mnt/workspace
sudo zpool status workspace
printf 'workspace survived\n' | sudo tee /mnt/workspace/check.txt
printf 'ephemeral\n' | sudo tee /etc/reset-check
sudo zpool export workspace
sudo reboot
```

After reboot, verify `/etc/reset-check` **doesn't exist**. Then run `/usr/local/sbin/mount-workspace` again and confirm `/mnt/workspace/check.txt` **does exist**. `zpool status` should show `ONLINE` when both images are attached. Always export the ZFS pool before closing the VM or removing a drive.

**Test each boot path:** shut down the VM, use a fresh copy of OVMF variables, and boot A alone and B alone. Test degraded reads with a read-only import (docs/VALIDATION.md); then test degraded writes on only A and rejoin B to resilver. Never run two VMs against opposite members. `live-media=` first tries the specified ESP but Debian live-boot can fall back to another medium if it is unusable; A-only/B-only tests establish actual independence.

## 4. Flash to physical USB drives

Return to the repository root. Do not attach the original raw images or a second flashed copy of the same member while working with physical drives: their duplicated GPT/PARTUUID/ZFS identities can collide. Plug in both USB drives. Identify **whole devices** by serial and size:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
```

For example, if you **verified** that `/dev/sdb` and `/dev/sdc` are the correct USB sticks:

```bash
# DESTRUCTIVE: removes all existing contents on the named USB devices.
sudo umount /dev/sdb?* /dev/sdc?* 2>/dev/null || true
sudo dd if=out/usb-a.img of=/dev/sdb bs=4M conv=fsync status=progress
sudo dd if=out/usb-b.img of=/dev/sdc bs=4M conv=fsync status=progress
sync
# Relocate the backup GPT to the real end of each larger physical disk.
sudo sgdisk -e /dev/sdb
sudo sgdisk -e /dev/sdc
sudo partprobe /dev/sdb /dev/sdc
```

Use the whole disks (`/dev/sdb`, `/dev/sdc`), never individual partitions. **Replace these example names** with your actual devices. For NVMe disk examples use `/dev/nvme0n1` as the whole device, not `/dev/nvme0n1p1`.

`sgdisk -e` repairs GPT placement after copying to larger media; it does not resize partitions. The image contains a fixed ~8 GiB workspace partition. Extra capacity on a larger USB remains unallocated. If you want to consume the entire USB, resize **partition 2** and expand the ZFS vdev carefully after the system has been tested; do not alter partition 1 or assume resizing a degraded pair is safe.

## 5. Day-to-day use

Boot from either USB via your machine's UEFI boot menu. Once the console is up, the ZFS workspace should **not** be mounted:

```bash
findmnt /mnt/workspace || true
sudo zpool list
```

Mount explicitly:

```bash
sudo /usr/local/sbin/mount-workspace
```

The script checks root privileges, loads the ZFS module, generates a per-boot random `/etc/hostid` if missing, discovers pool members by GPT PARTUUID, imports the pool **without auto-mounting**, and mounts `workspace/data`. It refuses to silently force-import a pool that appears to belong to another host. It serializes calls, checks the dataset mountpoint and noauto property, refuses a different filesystem or non-empty mountpoint, and can be rerun when the expected dataset is already mounted. It does not start ZED; use `sudo systemctl start zfs-zed.service` after mounting if event handling is desired.

Before powering off, removing media, or switching machines:

```bash
cd /
sudo zpool sync workspace
sudo zpool export workspace
sudo poweroff
```

If `zpool export` reports the pool is busy, check `findmnt`, open files, current working directories, or services using the workspace. Do not forcefully remove USB sticks while they are being written.

## 6. Degraded operation and recovery

### One USB is missing

ZFS can generally import the mirror degraded if one valid member exists. `mount-workspace` does not require two members. After import:

```bash
sudo zpool status workspace
```

Expect `DEGRADED` and one `UNAVAIL` member. You may write to the remaining member **only if you will not then write independently to the stale USB**.

### Rejoin the missing USB

With the pool exported, connect both sticks to **one host** and run `sudo mount-workspace`, then:

```bash
sudo zpool status -P workspace
sudo zpool wait -t resilver workspace
sudo zpool status workspace
```

**`zpool wait` returning is not proof of successful recovery:** it also returns when no resilver is running, or work was canceled/paused. Verify both members are ONLINE and inspect the final status/error counters. The vdev may resilver automatically after reconnecting. If the missing member remains `UNAVAIL`, inspect the GPT PARTUUIDs, the device list, and ZFS vdev GUIDs before using `zpool online` or `zpool replace`. Do not guess device names or use the stale member to create a fresh pool.

### Import says "potentially active" or "last accessed by another system"

This is a protection, not a nuisance. First confirm the pool is **not mounted or imported anywhere else**, and neither separated member has been written independently. Only then consider a *manual* force-import:

```bash
sudo zpool import -d /dev/disk/by-partuuid  # inspect available pools
sudo zpool import -f -N -d /dev/disk/by-partuuid -o cachefile=none POOL_GUID_FROM_BUILD_INFO
sudo /usr/local/sbin/mount-workspace
```

Replace `POOL_GUID_FROM_BUILD_INFO` with the numeric GUID you verified from the manifest and import listing. Generate a hostid with `sudo zgenhostid` first if missing. The mount script itself deliberately does **not** use `-f`. Never force an import if a second host may be accessing the pool. If two members have divergent histories, **stop** and decide which copy is authoritative before doing anything that could change pool labels; there is no automatic merge.

### Module missing

If `modprobe zfs` fails, the live image did not include a usable ZFS module for its running kernel (or boot firmware is enforcing module signing). Check:

```bash
uname -r
find /lib/modules/"$(uname -r)" -iname 'zfs.ko*'
dmesg | tail -100
```

Do not attempt to build DKMS modules at every live boot: fix the image build so the compiled module is already in SquashFS.

## 7. Updating the OS without destroying workspace

**Never reflash the complete image if you need to preserve existing ZFS data.** A full `dd` overwrites the ZFS partition with an old mirror-member image.

Build a new ISO, export the pool, and attach the USBs to another Linux host. Replace **only** partition 1's payload, preserving its filesystem UUID and existing GRUB configuration. Partition 2 and its PARTUUID must remain untouched.

The original `rsync -a` example is unsuitable for FAT32: do not preserve Unix metadata or symlinks. First stage the new kernel and initrd as regular files on a Linux filesystem, checking the selected kernel against the new image's manifest. The ISO may expose either versioned names or aliases. Example with a verified ISO and existing ESP:

```bash
sudo mkdir -p /mnt/newiso /mnt/esp
sudo mount -o loop,ro build/live-image-amd64.hybrid.iso /mnt/newiso
sudo mount /dev/sdb1 /mnt/esp
kernel=$(sudo unsquashfs -cat /mnt/newiso/live/filesystem.squashfs usr/local/share/level1vault/kernel-version)
stage=$(mktemp -d)
kernel_file=/mnt/newiso/live/vmlinuz-$kernel
initrd_file=/mnt/newiso/live/initrd.img-$kernel
[ -f "$kernel_file" ] || kernel_file=/mnt/newiso/live/vmlinuz
[ -f "$initrd_file" ] || initrd_file=/mnt/newiso/live/initrd.img
cp -L "$kernel_file" "$stage/vmlinuz"
cp -L "$initrd_file" "$stage/initrd.img"
cp /mnt/newiso/live/filesystem.squashfs "$stage/"
sudo unsquashfs -cat /mnt/newiso/live/filesystem.squashfs usr/local/share/level1vault/packages.tsv > "$stage/packages.tsv"
# Check free space and the FAT32 4 GiB per-file limit before copying.
df -h /mnt/esp
ls -lh "$stage/"
# Preserve a backup of the previous /live directory elsewhere before this step.
sudo rsync -rt --delete "$stage/" /mnt/esp/live/
# Preserve optional live-boot UUID markers with the new payload.
sudo rm -rf /mnt/esp/.disk
if [ -d /mnt/newiso/.disk ]; then sudo cp -rL /mnt/newiso/.disk /mnt/esp/; fi
sync
sudo umount /mnt/esp /mnt/newiso
rm -r "$stage"
```

This copy is **not atomic**: interruption can render that USB unbootable. Update and verify one member's OS before updating the other; keep an offline backup of its old boot payload. Run with `set -e` and arrange cleanup on failure. Repeat for USB B's partition 1. Do not upgrade pool features until both boot environments support them. If you alter bootloader behavior, rebuild/update GRUB separately with the correct ESP UUID.

## 8. Operational caveats

- **Not a security boundary:** the FAT32 ESP holding SquashFS can be edited by someone with raw USB access. The *running root* is stateless, but the USB contents are not physically write-protected or cryptographically verified.
- **Boot only UEFI x86-64:** this image does not implement legacy BIOS boot. Secure Boot needs signing work not included here.
- **Hardware:** typical flash sticks may be poor ZFS mirror members due to write endurance and unreliable flush semantics; prefer good-quality USB SSDs for important data, and always maintain a separate backup.
- **Memory:** live root modifications consume RAM. The ZFS ARC also consumes RAM. Test on the minimum RAM size you want to support.
- **Identity:** no static `/etc/hostid` is shipped. A new per-boot hostid is generated when mounting. Clean `zpool export` is important. This isn't a replacement for fencing.
- **Reproducibility:** the configuration and process are version-controlled, but default Debian mirrors change. See docs/REVIEW.md for snapshot pinning. UUIDs, ZFS GUIDs and build timestamps deliberately differ between newly assembled pairs; raw images are not byte-identical reproducible outputs or reusable workspace templates.
- **Real-world testing:** this revision was reviewed against Debian/OpenZFS documentation and checked with shell syntax and mocked behavior tests; they have **not** been executed through a complete image build and hardware boot in this environment. Do the QEMU test before trusting the media.

## References

- Debian Live Manual: https://live-team.pages.debian.net/live-manual/html/live-manual.en.html
- Debian live-build: https://manpages.debian.org/trixie/live-build/lb_config.1.en.html
- Debian live-boot: https://manpages.debian.org/trixie/live-boot-doc/live-boot.7.en.html
- OpenZFS Debian installation: https://openzfs.github.io/openzfs-docs/Getting%20Started/Debian/index.html
- OpenZFS import: https://openzfs.github.io/openzfs-docs/man/master/8/zpool-import.8.html

- ZFS dataset properties (noauto): https://manpages.debian.org/trixie/zfsutils-linux/zfsprops.7.en.html
- ZFS systemd generator: https://manpages.debian.org/trixie/zfsutils-linux/zfs-mount-generator.8.en.html
- GRUB standalone EFI image: https://manpages.debian.org/trixie/grub-common/grub-mkstandalone.1.en.html
- Debian live-config: https://manpages.debian.org/trixie/live-config-doc/live-config.7.en.html
