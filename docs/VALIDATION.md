# Acceptance checklist

## Automated coverage

- Shell syntax and ShellCheck across builders, Docker entrypoint, live hook/runtime helper and assembly test.
- Seventeen mocked behavior tests, including host-pool conflict refusal, checksum failure before host mutations, existing signature rejection, initialization rerun refusal, export-failure loop retention and the conservative pool feature arguments.
- CI on Ubuntu 22.04 builds the actual Debian Trixie toolchain container and runs file-only assembly **without privileged mode**. A small synthetic ISO exercises real GPT creation, FAT population, per-drive GRUB EFI output, metadata checksums, medium UUID markers, unique identifiers and empty data partitions.

The synthetic kernel/initrd are dummy payloads: this test does not boot Linux or compile DKMS. This workspace has no Docker/loop/KVM runtime. Full live-build, real host ZFS initialization and UEFI/hardware tests remain required; passing CI must not be interpreted as their completion.

## Required Ubuntu + Docker build checks

1. On Ubuntu 22.04, the existing zpool/zfs tools communicate with its running module. Existing host pools remain unaffected throughout the workflow.
2. `sudo ./build-container.sh "$PWD/build"` completes a real live-build inside Trixie. Capture build.log and builder-image.json. The hook verifies one Debian target kernel and matching ZFS DKMS module even though container uname reports Ubuntu's kernel.
3. Inspect SquashFS: matching module under /lib/modules/<Debian-kernel>, no build-host hostid/cache, masked import/mount services and generator, executable mount-workspace, no zfs-initramfs package.
4. Inspect initramfs with lsinitramfs: live-boot and required USB/FAT drivers present. ZFS is data only, not a root-boot dependency.
5. File assembly leaves IMAGES-PREPARED and PREPARED-SHA256SUMS, with distinct ESP UUIDs/data PARTUUIDs in images.json. Neither data partition has a ZFS signature yet; no host loop devices were needed.
6. On the host, run 03-initialize-workspace.sh. Verify one mirror is created, only lz4_compress is enabled, workspace/data is noauto, the pool is exported, loops detached, and READY/final SHA256SUMS/WORKSPACE-INFO.txt exist.
7. Check final checksums before VM tests. Checksums must be refreshed after VM writes, with pool exported and VM stopped.

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

Read-only test (after confirming no other owner, substitute numeric pool GUID from WORKSPACE-INFO.txt):

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
