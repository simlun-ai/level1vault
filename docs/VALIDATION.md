# Acceptance checklist

## Automated coverage

- `make test-qemu PAIR=N` implements workstation-only UEFI boot, reboot persistence/overlay reset, encrypted mounting, ownership, independent A/B boot, degraded writes and rejoin/scrub checks on disposable image copies. CI does not launch VMs; it only lints and unit-tests the runner and checks documentation consistency. See [QEMU testing](QEMU.md) and the [shared manual checks](../stages/04-test-pair/README.md); implementation and mocked runner tests alone do not constitute a successful VM run. Record the first successful workstation suite run below once verified.
- Account/password verification, real PAM login/MOTD display and passwordless sudo/PATH checks in a disposable Trixie container.
- Shell syntax and ShellCheck across builders, Docker entrypoint, live hook/runtime helper and assembly test.
- Mocked behavior tests, including host-pool conflict refusal, incomplete-image refusal before host mutations and USB readback corruption detection, existing signature rejection, initialization rerun refusal, export-failure loop retention and the conservative pool feature arguments.
- CI on Ubuntu 22.04 builds the actual Debian Trixie toolchain container and runs file-only assembly **without privileged mode**. A small synthetic ISO exercises real GPT creation, FAT population, per-drive GRUB EFI output, metadata, medium UUID markers, unique identifiers and empty data partitions.

Real ZFS integration [passed on Ubuntu 22.04](https://github.com/simlun-ai/level1vault/actions/runs/38711305462) using newly created small GPT image pairs and loop devices. The numbered-pair test uses IDs 1, 2, 3 and 10: encrypted initialization, wrong/correct stdin passphrases, UID/GID 1000 writes, shared full/incremental checkpoints, unloaded source keys, independent per-destination holds, interrupted/resumed full and incremental receives, read-only replica mounting and divergent/mounted destination refusal. It exercises in-place numeric failover 1→2→3, fencing/observable-active-owner refusal, explicit source rebinding with the existing destination dataset GUID preserved, and preservation/refusal of a returning former active pair with unreplicated writes. The test only writes new file-backed mirrors on a disposable runner, not physical USB devices, and requires an explicit disposable-test flag.

The synthetic kernel/initrd are dummy payloads: that assembly test does not boot Linux or compile DKMS.

## Full build verified on 2026-10-14

[Full Debian Live build on Ubuntu 22.04](https://github.com/simlun-ai/level1vault/actions/runs/38636756155) passed in approximately 17 minutes. It built the actual ISO and both default 12 GiB images, then extracted SquashFS and inspected:

- ZFS module presence and vermagic matching target kernel `6.12.111+deb13-amd64`.
- Executable workspace-mount and absence of baked-in hostid/pool cache.
- Masked automatic pool import/mount services and mount generator.
- Live-boot components in the actual initramfs.
- Prepared-image state markers and distinct image identities.

The workflow keeps diagnostic logs/manifests as an Actions artifact. It can also be rerun manually from GitHub Actions. Kernel/package versions depend on the configured Debian mirrors; this records one verified build, not a package pin.

The operator verified the [QEMU procedure](QEMU.md), including the normal guest boot/mount/reboot sequence, on 2026-10-15. QEMU required desktop-user execution and writable image/OVMF files; no kvm group change was needed. The operator subsequently confirmed each physical USB boots independently on a laptop after disabling Secure Boot, and the ZFS mirror works with both sticks connected. This confirms basic physical boot and both-member mirror operation. Physical degraded-write/rejoin and the new numbered-pair failover workflow remain unconfirmed. Continue with the checks below; a successful module presence/vermagic check does not prove it loads on the target machine.

## Required Linux host + Docker build checks

1. On the Linux host, the existing zpool/zfs tools communicate with its running module. Existing host pools remain unaffected throughout the workflow.
2. `make build` completes a real live-build inside Trixie. Capture build.log, builder-info.txt and build-metadata.json. The hook verifies one Debian target kernel and matching ZFS DKMS module even though container uname reports the host's kernel.
3. Inspect SquashFS: matching module under /lib/modules/<Debian-kernel>, no build-host hostid/cache, masked import/mount services and generator, executable workspace-mount, no zfs-initramfs package.
4. Inspect initramfs with lsinitramfs: live-boot and required USB/FAT drivers present. ZFS is data only, not a root-boot dependency.
5. File assembly leaves IMAGES-PREPARED, with distinct ESP UUIDs/data PARTUUIDs in images.json. Neither data partition has a ZFS signature yet; no host loop devices were needed.
6. On the host, run stages/03-initialize-pair/initialize.sh. Verify one mirror is created, LZ4 plus encryption and its required dependencies are enabled, level1-workspace-1/data is noauto, the pool is exported, loops detached, and READY/WORKSPACE-INFO.txt exist.
7. Check completion markers before VM tests. Before USB writing, stop VMs, export the pool and detach loops. USB writes are verified by reading back the written data.

## Verify the new account and helpers after rebuilding

```bash
id simlun                         # uid=1000, primary gid=1000
sudo -n id -u                      # 0, without prompting
command -v workspace-mount workspace-umount workspace-sync
cat /etc/motd                     # workspace command quick guide
sudo workspace-mount 1             # enter the encryption passphrase
stat -c '%u:%g %a' /mnt/workspace # 1000:1000 750
sudo -iu simlun sh -c 'printf "owned by simlun\n" > /mnt/workspace/simlun-write-test'
cd /
sudo workspace-umount
```

Confirm console autologin selects root and `start` opens tmux as simlun with the mounted persistent home. Test exiting and detaching separately, refusal to export with remaining sessions, and both Y/n prompts. Verify home changes survive reboot; new image defaults must not overwrite an existing home. The simlun account retains password `password` and passwordless sudo. On an already-booted older image the old account and helper names persist until its OS payload is updated and rebooted.

## VM matrix

Keep the raw images on only one machine/VM owner. Export before shutting down. Use fresh OVMF vars for independent boot cases. Both files must never be writable by concurrent VMs or by the build host while QEMU owns them.

| Case | Required outcome |
| --- | --- |
| A alone, clean firmware vars | Boots to usable console without importing ZFS; running root is overlay with SquashFS lower layer. |
| B alone, clean firmware vars | Same, independently of A's ESP. |
| Both disks | No workspace pool imported before manual command; helper imports ONLINE mirror, mounts only level1-workspace-1/data. |
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
sudo zfs load-key -L prompt level1-workspace-1/data
sudo zfs mount level1-workspace-1/data
cat /mnt/workspace/check.txt
sudo zpool status -gP level1-workspace-1
cd /
sudo zpool export level1-workspace-1
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
sudo /usr/local/sbin/workspace-mount 1
sudo zfs get mountpoint,canmount,mounted,encryption,keyformat,keystatus,org.openzfs.systemd:ignore level1-workspace-1/data
sudo zpool status -gP level1-workspace-1
```

To test USB presentation, replace each `-drive if=virtio,...` in the [QEMU command](QEMU.md) with:

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

## N-pair and ordered-failover acceptance after rebuilding

Follow [PAIRS.md](PAIRS.md) to provision unique numbered pairs. Exercise at least four IDs, including 2 and 10, to verify numeric order. Verify shared-checkpoint full and incremental receives, passphrase unlock, UID/GID 1000 ownership, read-only replica use, both members ONLINE and clean scrubs. Rotate one offsite copy at a time. On disposable images, interrupt and resume full/incremental transfers, fence/export the active pair, activate the next numeric ID in place, make UID 1000 writes, and rebind the remaining replicas using their common checkpoint. Repeat failover and verify names/identities stay stable. Confirm incomplete receives, an observably active former owner, absent fencing acknowledgement, divergent writes/snapshots and source changes without --rebind all fail safely. Record completed checkpoints, IDs and physical-device locations outside the pools.

## BuildKit refactor acceptance

The historical runs above validated the earlier Docker-run pipeline. The BuildKit PR must additionally pass sandboxed test stages, two scratch/local exports with distinct identities, and a complete privileged live-build plus exported-ISO inspection. No boot/runtime code changes are intended. Operator boot tests of the new output and measured disk usage remain acceptance checks.

