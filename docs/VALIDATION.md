# Acceptance checklist

## Checks completed in this workspace

- Shell syntax checks for both builders, the hook and runtime helper.
- Ten behavior tests (mocked system tools): config generation/overwrite refusal, first import, repeat mount, import refusal, foreign mount, wrong mountpoint/noauto property, nonempty mountpoint, root check, and export/unmount failure cleanup.
- Review against Debian 13 and OpenZFS manuals.

Not available here: live-build, debootstrap, shellcheck, QEMU/OVMF, loop devices, /dev/kvm, or a Debian 13 host. Thus no ISO was built, no real ZFS pool created, no real EFI loader executed, and no hardware tests performed. GitHub Actions runs shellcheck and these mock tests; it does not build/boot disk images.

## Required build checks in Debian 13

1. Host zpool --version reports matching userland and loaded module versions. Running kernel headers exist; modprobe zfs works.
2. Configuration build finishes with pipefail enabled. Capture build.log and the live-build package list. Hook reports exactly one kernel and ZFS DKMS success.
3. Inspect SquashFS: matching ZFS module under /lib/modules/<kernel>, no /etc/hostid or zpool.cache, masked import/mount units and generator override, executable mount-workspace, no zfs-initramfs package.
4. Inspect initramfs with lsinitramfs: live-boot scripts and disk/FAT/USB drivers available, no build-host hostid/cache or ZFS-root boot flow. Data ZFS need not be in initramfs.
5. Assembly finishes with READY present, two distinct FAT UUIDs and PARTUUIDs, one pool GUID, correct partition sizes, and valid SHA256SUMS. Check sfdisk --verify on each image.
6. Mount each ESP read-only: all three live payload files are regular files and EFI/BOOT/BOOTX64.EFI exists. No host pool or loop mappings remain after assembly.

## VM matrix

Keep the raw images on only one machine/VM owner. Export before shutting down. Use fresh OVMF vars for independent boot cases. Both files must never be writable by concurrent VMs or by the build host while QEMU owns them.

| Case | Required outcome |
| --- | --- |
| A alone, clean firmware vars | Boots to usable console without importing ZFS; running root is overlay with SquashFS lower layer. |
| B alone, clean firmware vars | Same, independently of A's ESP. |
| Both disks | No workspace pool imported before manual command; helper imports ONLINE mirror, mounts only workspace/data. |
| Repeat helper | No second import, correct existing mount reported. |
| Reboot | /etc/reset-check disappears; workspace test data survives clean export/import. |
| A alone, read-only degraded import | Reads previously committed test data; pool is DEGRADED; does not modify its history. |
| B alone, read-only degraded import | Same. |
| A alone, degraded writes | New file written only through A, then clean export. |
| Rejoin B | File visible; resilver completes; both ONLINE with no uncorrected errors. Then clean export. |
| B alone after resilver | Read-only import sees the file written during A-only operation. |
| USB emulation | Both images boot and discover FAT/PARTUUID devices as USB rather than only virtio. |

Read-only test (after confirming no other owner, substitute numeric pool GUID):

```bash
sudo modprobe zfs
[ -s /etc/hostid ] || sudo zgenhostid
sudo zpool import -N -d /dev/disk/by-partuuid -o cachefile=none -o readonly=on POOL_GUID
sudo zfs mount workspace/data
cat /mnt/workspace/check.txt
sudo zpool status -gP workspace
cd /
sudo zpool export workspace
```

If active-host protection refuses an import, investigate first; only use -f after establishing exclusive ownership and authoritative history.

Guest diagnostic commands:

```bash
cat /proc/cmdline
findmnt -t overlay,squashfs,tmpfs
uname -r
sudo modprobe zfs
zpool --version
modinfo -F vermagic zfs
systemctl is-enabled zfs-import-cache.service zfs-import-scan.service zfs-mount.service
ls -l /etc/systemd/system-generators/zfs-mount-generator
ip -br link
ip -br addr
sudo zpool list
sudo /usr/local/sbin/mount-workspace
sudo zfs get mountpoint,canmount,mounted,org.openzfs.systemd:ignore workspace/data
sudo zpool status -gP workspace
```

To test USB presentation, replace each `-drive if=virtio,...` in the README command with:

```bash
-device qemu-xhci,id=xhci \
-drive if=none,id=usba,format=raw,file=./usb-a.img \
-device usb-storage,bus=xhci.0,drive=usba \
-drive if=none,id=usbb,format=raw,file=./usb-b.img \
-device usb-storage,bus=xhci.0,drive=usbb
```

For single-image cases omit both the drive and device for the absent member. Do not use QEMU snapshot mode to test persistent writes/resilver: its temporary changes are discarded.

## Physical acceptance

Repeat A-only, B-only, both-member and degraded/rejoin cases on the intended UEFI machines with Secure Boot disabled. Confirm working NIC/storage firmware, USB stability under sustained writes, cold boot, minimum RAM, successful clean export, and boot-only recovery when the workspace is unavailable. Do not unplug the running boot medium: the SquashFS is normally still read from it. Always shut down/export before moving sticks.

Unexpected power-loss recovery should use a separate disposable image pair; it can require explicit force import because this image's hostid changes each boot. A successful boot alone does not prove the ZFS workspace is healthy.
