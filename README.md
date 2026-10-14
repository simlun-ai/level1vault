# Runbook: dual Debian Live USB images with a ZFS mirror

**Target:** Debian 13 Trixie amd64, x86-64 UEFI, Secure Boot **disabled**. Run on a disposable Debian 13 build VM. The two sparse outputs are 12 GiB each: approximately 4 GiB ESP/FAT32 containing GRUB and the live filesystem, and approximately 8 GiB ZFS workspace partition. Requires two USB drives of at least 12 GiB each (16 GiB or larger recommended).

**Goal:** both USB sticks are individually bootable; the Debian root is a non-persistent live OS; the writable ZFS workspace is a two-way mirror when both drives are present. The dataset mounts **only** when `/usr/local/sbin/mount-workspace` is called.

**Crucial limitation:** ZFS mirroring requires a single active pool owner. Never mount/read-write each separated USB on different hosts and then try to merge their changes; that is not supported. A disconnected, formerly writable mirror member is stale and must resilver before being booted and written independently.

## 0. Host preparation

Use Debian 13 amd64 in a VM or disposable workstation. Have at least 30 GiB free and enough space for the live-build chroot and caches. The system running the build must have a loadable ZFS module; a container without `/lib/modules` support is not sufficient. Secure Boot should be disabled on the target UEFI computers; these directions do not sign the DKMS module or EFI loader.

Enable Debian `contrib` in your apt sources. On a default Debian 13 deb822 apt layout:

```bash
sudo sed -i -E '/^Components:/ { /(^|[[:space:]])contrib([[:space:]]|$)/! s/$/ contrib/; }' /etc/apt/sources.list.d/debian.sources
sudo apt update
sudo apt install -y \
    live-build debootstrap xorriso squashfs-tools \
    dosfstools mtools isolinux syslinux-common \
    grub-efi-amd64-bin grub2-common grub-common \
    parted gdisk util-linux rsync qemu-system-x86 ovmf \
    linux-headers-amd64 dkms zfs-dkms zfsutils-linux
sudo modprobe zfs
zpool --version
```

If your VM uses a traditional `sources.list`, enable the `contrib` component there instead; run `apt update` before installing. The build is architecture-specific (`amd64`). If `modprobe zfs` fails, repair the host's DKMS/kernel header installation before continuing. `zfs-dkms` is sourced from Debian `contrib`.

## 1. Build the Debian Live ISO

Unpack/copy the runbook's two shell scripts into the working area, then:

```bash
mkdir -p ~/debian-zfs-live
cd ~/debian-zfs-live
cp /path/to/01-configure-live.sh .
cp /path/to/02-assemble-images.sh .
./01-configure-live.sh
sudo lb build 2>&1 | tee build.log
```

The setup script creates:

```text
config/
├── package-lists/appliance.list.chroot
├── includes.chroot/usr/local/sbin/mount-workspace
└── hooks/live/90-zfs.hook.chroot
```

`appliance.list.chroot` includes `zfs-dkms`, `zfsutils-linux`, sudo, and basic CLI packages. The `--linux-packages 'linux-image linux-headers'` setting ensures matching target kernel headers. The build hook runs `dkms autoinstall`, validates the ZFS kernel module for each live kernel, masks automatic pool-import services, and removes `/etc/hostid` from the immutable image. This is important: **installing ZFS userland by itself is not enough**.

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

From `~/debian-zfs-live`:

```bash
sudo ./02-assemble-images.sh "$PWD/live-image-amd64.hybrid.iso"
```

The script performs all of these operations, rather than leaving partitioning to you:

1. Refuses to overwrite existing `usb-a.img` or `usb-b.img` files.
2. Creates two sparse 12 GiB raw disk images.
3. Attaches them as distinct loop devices and creates **unique GPTs**.
4. Creates partition 1 as a 4 GiB FAT32 ESP (`LIVE_A` or `LIVE_B`), and partition 2 as the remaining ZFS member.
5. Copies `/live` from the ISO to each ESP.
6. Installs UEFI GRUB in the removable-media path (`EFI/BOOT/BOOTX64.EFI`) on each ESP, without changing the build machine's UEFI NVRAM.
7. Generates each GRUB menu to select its own FAT volume using its unique filesystem UUID. It also passes `boot=live components nopersistence` and `live-media=` to Debian live-boot.
8. Creates **one pool**, `workspace`, mirrored over both loop partitions, with `ashift=12` and `cachefile=none`.
9. Creates **one dataset**, `workspace/data`, configured to mount at `/mnt/workspace` and with LZ4 compression.
10. Exports the pool, unmounts and detaches the loop devices.

```bash
ls -lh usb-a.img usb-b.img
sudo sfdisk -l usb-a.img
sudo sfdisk -l usb-b.img
```

Note: each 12 GiB image contains its own bootable OS plus a **mirror member**, not a standalone independently initialized ZFS pool. Never run `zpool create` separately for the second image.

## 3. Test both images in QEMU before flashing

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

If KVM isn't available, use `-machine q35,accel=tcg` (slower). The default Debian Live console user is normally `user`, and the standard default password is `live`; `live-config` configures its sudo access and console autologin. Verify these defaults in the guest before relying on unattended operation.

Inside the booted live guest:

```bash
cat /proc/cmdline
uname -r
sudo modprobe zfs
modinfo zfs | head
sudo zpool import -d /dev/disk/by-partuuid
sudo /usr/local/sbin/mount-workspace
findmnt /mnt/workspace
sudo zpool status workspace
printf 'workspace survived\n' | sudo tee /mnt/workspace/check.txt
printf 'ephemeral\n' | sudo tee /etc/reset-check
sudo zpool export workspace
sudo reboot
```

After reboot, verify `/etc/reset-check` **doesn't exist**. Then run `/usr/local/sbin/mount-workspace` again and confirm `/mnt/workspace/check.txt` **does exist**. `zpool status` should show `ONLINE` when both images are attached. Always export the ZFS pool before closing the VM or removing a drive.

**Test each boot path:** shut down the VM, then repeat the QEMU boot with `usb-b.img` first in the drive list (or as the sole drive). If using either image alone, do not modify workspace independently on both sides. Boot-only tests can omit mounting the pool.

## 4. Flash to physical USB drives

Plug in both USB drives. Identify **whole devices** by serial and size:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
```

For example, if you **verified** that `/dev/sdb` and `/dev/sdc` are the correct USB sticks:

```bash
# DESTRUCTIVE: removes all existing contents on the named USB devices.
sudo umount /dev/sdb?* /dev/sdc?* 2>/dev/null || true
sudo dd if=usb-a.img of=/dev/sdb bs=4M conv=fsync status=progress
sudo dd if=usb-b.img of=/dev/sdc bs=4M conv=fsync status=progress
sync
```

Use the whole disks (`/dev/sdb`, `/dev/sdc`), never individual partitions. **Replace these example names** with your actual devices. For NVMe disk examples use `/dev/nvme0n1` as the whole device, not `/dev/nvme0n1p1`.

The image writes only a fixed ~8 GiB workspace partition. Extra capacity on a larger USB remains unallocated. If you want to consume the entire USB, resize **partition 2** and expand the ZFS vdev carefully after the system has been tested; do not alter partition 1 or assume resizing a degraded pair is safe.

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

The script checks root privileges, loads the ZFS module, generates a per-boot random `/etc/hostid` if missing, discovers pool members by GPT PARTUUID, imports the pool **without auto-mounting**, and mounts `workspace/data`. It refuses to silently force-import a pool that appears to belong to another host. It can safely be rerun when the expected dataset is already mounted.

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

The vdev may resilver automatically after reconnecting. If the missing member remains `UNAVAIL`, inspect the GPT PARTUUIDs, the device list, and ZFS vdev GUIDs before using `zpool online` or `zpool replace`. Do not guess device names or use the stale member to create a fresh pool.

### Import says "potentially active" or "last accessed by another system"

This is a protection, not a nuisance. First confirm the pool is **not mounted or imported anywhere else**, and neither separated member has been written independently. Only then consider a *manual* force-import:

```bash
sudo zpool import -d /dev/disk/by-partuuid  # inspect available pools
sudo zpool import -f -N -d /dev/disk/by-partuuid -o cachefile=none workspace
sudo /usr/local/sbin/mount-workspace
```

The mount script itself deliberately does **not** use `-f`. Never force an import if a second host may be accessing the pool. If two members have divergent histories, **stop** and decide which copy is authoritative before doing anything that could change pool labels; there is no automatic merge.

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

Build a new ISO, export the pool, attach each USB to another Linux host, mount **only partition 1** (ESP) and replace its `/live` contents with those from the new ISO. The existing GRUB loader and config remain. Example for one verified USB disk where its ESP is `/dev/sdb1`:

```bash
sudo mkdir -p /mnt/newiso /mnt/esp
sudo mount -o loop,ro live-image-amd64.hybrid.iso /mnt/newiso
sudo mount /dev/sdb1 /mnt/esp
sudo rsync -a --delete /mnt/newiso/live/ /mnt/esp/live/
sync
sudo umount /mnt/esp /mnt/newiso
```

Repeat for USB B's partition 1. Do **not** touch either partition 2. If you change bootloader behavior, update GRUB separately and keep the per-ESP filesystem UUID references correct. Do not upgrade ZFS pool features until all boot environments can import that pool.

## 8. Operational caveats

- **Not a security boundary:** the FAT32 ESP holding SquashFS can be edited by someone with raw USB access. The *running root* is stateless, but the USB contents are not physically write-protected or cryptographically verified.
- **Boot only UEFI x86-64:** this image does not implement legacy BIOS boot. Secure Boot needs signing work not included here.
- **Hardware:** typical flash sticks may be poor ZFS mirror members due to write endurance and unreliable flush semantics; prefer good-quality USB SSDs for important data, and always maintain a separate backup.
- **Memory:** live root modifications consume RAM. The ZFS ARC also consumes RAM. Test on the minimum RAM size you want to support.
- **Identity:** no static `/etc/hostid` is shipped. A new per-boot hostid is generated when mounting. Clean `zpool export` is important. This isn't a replacement for fencing.
- **Reproducibility:** record Debian snapshot/repository versions if byte-for-byte rebuild reproducibility matters. `live-build` config alone does not pin package versions over time.
- **Real-world testing:** these instructions were prepared from Debian/OpenZFS documentation and shell-syntax checked; they have **not** been executed through a complete image build and hardware boot in this environment. Do the QEMU test before trusting the media.

## References

- Debian Live Manual: https://live-team.pages.debian.net/live-manual/html/live-manual.en.html
- Debian live-build: https://manpages.debian.org/trixie/live-build/lb_config.1.en.html
- Debian live-boot: https://manpages.debian.org/trixie/live-boot-doc/live-boot.7.en.html
- OpenZFS Debian installation: https://openzfs.github.io/openzfs-docs/Getting%20Started/Debian/index.html
- OpenZFS import: https://openzfs.github.io/openzfs-docs/man/master/8/zpool-import.8.html
