# Acceptance checklist

## Automated coverage

- Account/password verification and passwordless sudo/PATH checks in a disposable Trixie container.
- Shell syntax and ShellCheck across builders, Docker entrypoint, live hook/runtime helper and assembly test.
- Twenty-two mocked behavior tests, including host-pool conflict refusal, checksum failure before host mutations, existing signature rejection, initialization rerun refusal, export-failure loop retention and the conservative pool feature arguments.
- CI on Ubuntu 22.04 builds the actual Debian Trixie toolchain container and runs file-only assembly **without privileged mode**. A small synthetic ISO exercises real GPT creation, FAT population, per-drive GRUB EFI output, metadata checksums, medium UUID markers, unique identifiers and empty data partitions.

The synthetic kernel/initrd are dummy payloads: that assembly test does not boot Linux or compile DKMS.

## Full build verified on 2026-10-14

[Full Debian Live build on Ubuntu 22.04](https://github.com/simlun-ai/level1vault/actions/runs/38636756155) passed in approximately 17 minutes. It built the actual ISO and both default 12 GiB images, then extracted SquashFS and inspected:

- ZFS module presence and vermagic matching target kernel `6.12.111+deb13-amd64`.
- Executable workspace-mount and absence of baked-in hostid/pool cache.
- Masked automatic pool import/mount services and mount generator.
- Live-boot components in the actual initramfs.
- Prepared-image state markers and checksums for both images and metadata.

The workflow keeps diagnostic logs/manifests as an Actions artifact. It can also be rerun manually from GitHub Actions. Kernel/package versions depend on the configured Debian mirrors; this records one verified build, not a package pin.

The operator verified README section 4 in QEMU, including the normal guest boot/mount/reboot sequence, on 2026-10-15. QEMU required desktop-user execution and writable image/OVMF files; no kvm group change was needed. Host-owned checksum files require `sudo tee`. The new account/helper changes below still need a rebuilt-image boot check; physical USB and degraded/rejoin tests remain unconfirmed. Continue with the checks below; a successful module presence/vermagic check does not prove it loads on the target machine.

## Required Ubuntu + Docker build checks

1. On Ubuntu 22.04, the existing zpool/zfs tools communicate with its running module. Existing host pools remain unaffected throughout the workflow.
2. `sudo ./build-container.sh "$PWD/build"` completes a real live-build inside Trixie. Capture build.log and builder-image.json. The hook verifies one Debian target kernel and matching ZFS DKMS module even though container uname reports Ubuntu's kernel.
3. Inspect SquashFS: matching module under /lib/modules/<Debian-kernel>, no build-host hostid/cache, masked import/mount services and generator, executable workspace-mount, no zfs-initramfs package.
4. Inspect initramfs with lsinitramfs: live-boot and required USB/FAT drivers present. ZFS is data only, not a root-boot dependency.
5. File assembly leaves IMAGES-PREPARED and PREPARED-SHA256SUMS, with distinct ESP UUIDs/data PARTUUIDs in images.json. Neither data partition has a ZFS signature yet; no host loop devices were needed.
6. On the host, run 03-initialize-workspace.sh. Verify one mirror is created, only lz4_compress is enabled, workspace/data is noauto, the pool is exported, loops detached, and READY/final SHA256SUMS/WORKSPACE-INFO.txt exist.
7. Check final checksums before VM tests. Checksums must be refreshed after VM writes, with pool exported and VM stopped.

## Verify the new account and helpers after rebuilding

```bash
id simlun                         # uid=2000, primary gid=2000
sudo -n id -u                      # 0, without prompting
command -v workspace-mount workspace-umount
sudo workspace-mount
cd /
sudo workspace-umount
```

The login password is `password`. Confirm the existing console autologin selects simlun. On an already-booted older image the old account and helper names persist until its OS payload is updated and rebooted.

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
sudo /usr/local/sbin/workspace-mount
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
