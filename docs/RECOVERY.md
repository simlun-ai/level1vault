# Operation, recovery and OS updates

[Documentation index](README.md)

## Mounting and clean shutdown

`workspace-umount` takes the same lock as the mount helper, syncs and exports all imported numbered workspace pools without forcing. With a numeric pair ID it selects only that pair; the default selects all imported numbered pairs. It succeeds harmlessly when already exported. A busy/failed export exits nonzero; close applications and leave the workspace in every shell before retrying. It never powers off the machine itself.

```bash
sudo workspace-mount 1
# Before shutdown, removing drives or changing hosts:
cd /
sudo workspace-umount
sudo poweroff
```

The mount helper serializes calls, creates a per-boot hostid if absent, imports without mounting by scanning PARTUUIDs, validates the pair and encryption properties, loads the key through stdin if needed, then explicitly mounts only the selected data filesystem. `workspace-mount` without an ID requires exactly one complete connected pair with both distinct members; it reads labels without importing candidates. `workspace-mount PAIR_ID` selects that pair and uses its active/replica state; only one dataset can use `/mnt/workspace` at a time. It does not force imports. It may import a valid single member in degraded mode. A random hostid means an unclean shutdown may require a deliberate, fenced force import on the next boot.

Never write independently to separated members and expect a merge. After writes to A while B was missing, B is stale; reconnect to the authoritative pool owner, resilver, and confirm **both members ONLINE with no errors** before using B independently. `zpool wait -t resilver level1-workspace-1` alone is insufficient: it also returns when no resilver is running.

If import reports a potentially active pool, establish that no other machine/VM owns either member, then inspect the pool GUID from `WORKSPACE-INFO.txt` and the import listing. Only after resolving ownership/history may you manually use:

```bash
sudo modprobe zfs
[ -s /etc/hostid ] || sudo zgenhostid
sudo zpool import -d /dev/disk/by-partuuid
sudo zpool import -f -N -d /dev/disk/by-partuuid -o cachefile=none VERIFIED_POOL_GUID
sudo workspace-mount 1
```

For additional pairs, periodic `workspace-sync SOURCE_ID TARGET_ID...`, snapshot retention and ordered in-place failover, use [pair operations](PAIRS.md).

For online/replacement procedures and divergent-history restrictions, see [member recovery](#member-recovery). Never run `zpool create` on a surviving member, `zpool add` to replace a mirror member, or `zpool upgrade` just to dismiss the expected feature warning. Do not unplug the running boot medium: SquashFS is still read from it unless explicitly copied to RAM.

## Member recovery

Before every recovery: stop all other pool owners, disconnect duplicate image/physical copies, retain backups, identify authoritative data. Preserve the original build manifest outside the pool.

For a formerly disconnected **original member**, reattach both devices on the authoritative host and import with the mount helper. Inspect:

```bash
sudo zpool status -gP level1-workspace-1
lsblk -o PATH,SIZE,FSTYPE,PARTUUID,UUID,MODEL,SERIAL
ls -l /dev/disk/by-partuuid/
```

If a known member remains unavailable, identify its numeric vdev GUID in status and its matching PARTUUID from images.json. After confirming they are the same original member, try bringing it online:

```bash
sudo zpool online level1-workspace-1 EXISTING_MEMBER_GUID
sudo zpool wait -t resilver level1-workspace-1
sudo zpool status -gP level1-workspace-1
```

If its path cannot be resolved, do not blindly replace it with a stale copy. Inspect labels with `sudo zdb -l /dev/disk/by-partuuid/VERIFIED_PARTUUID`; zdb is supplied by zfsutils-linux. A normal replacement is for a genuinely failed/lost device, not an attempt to reconcile independently written members.

### Replace failed drive 1A using surviving drive 1B

This example assumes pair **1** is the active workspace, **1A has died**, **1B holds the authoritative data**, and you have one blank USB drive to become the new **1A**. The existing pool, encryption and passphrase are preserved; ZFS copies the workspace from 1B to the replacement.

#### 1. Secure the survivor and record the original identities

Stop applications using the workspace, leave it in every shell, and export it with `sudo workspace-umount 1`. Shut down before unplugging the boot drive. Remove failed 1A and keep it disconnected throughout recovery. Fence other hosts/VMs and disconnect image loops or cloned copies of pair 1.

Keep a current independent backup if possible: until replacement finishes, 1B is the only working mirror member. Save the original pair's `images.json` and `WORKSPACE-INFO.txt` outside the pool. They identify the original A/B PARTUUIDs and pool GUID. Record 1B's physical serial number.

#### 2. Prepare a fresh, uninitialized boot image on the workstation

Use a separate **Linux amd64 workstation OS**, with the repository and the original build directory/ISO available. Leave surviving **1B disconnected while writing the replacement**. Reuse the ISO that boots 1B so the ZFS/kernel versions match.

From the repository root, choose a new output directory beneath that build directory:

```bash
build_dir=build/default
replacement_out="$build_dir/recovery-1a"
make assemble PAIR=1 BUILD_DIR="$build_dir" OUT="$replacement_out"
test -f "$replacement_out/IMAGES-PREPARED"
test ! -e "$replacement_out/READY"
test ! -e "$replacement_out/INITIALIZING"
```

Set `build_dir` to your original `BUILD_DIR` if different. Use a new `replacement_out` for each attempt; assembly refuses an existing directory. If the original ISO is unavailable, `make build PAIR=1 BUILD_DIR=build/recovery-os` creates a new ISO and uninitialized images; use `build/recovery-os/pairs/1` as `replacement_out` and ensure that OS supports the surviving pool.

Assembly creates two bootable images with fresh GPT, partition and FAT identities and matching GRUB configuration. Use **only the new `usb-a.img`**; its ZFS partition is empty. The unused `usb-b.img` is not a replacement for surviving 1B.

**Do not run `make initialize`, `make add-pair` or `make prepare-pairs` on these images. Do not use `make write-usb`: it writes both drives and requires initialized images.** An initialized image contains a separate pool, not an empty replacement member. Do not clone 1B or reuse the original initialized `usb-a.img`.

Record the new A identities from the fresh manifest:

```bash
python3 - "$replacement_out/images.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
print("Replacement 1A:", m["images"]["a"])
print("Image bytes:", m["image_mib"] * 1024 * 1024)
print("ESP MiB:", m["esp_mib"])
PY
```

The new USB must fit the entire image, and its new ZFS partition must be at least as large as the failed member's partition. The standard image is 12 GiB with a 4 GiB ESP; matching the original layout satisfies this for standard, unexpanded pairs. If your old workspace partitions were enlarged or used a custom layout, stop and prepare a sufficiently large empty replacement partition before proceeding; a larger physical stick alone does not enlarge the partition in this image.

#### 3. Write and verify only the blank replacement USB

Connect the blank USB. Identify its **whole-disk path by model, serial and size**:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
```

Here **`/dev/sda` must be the blank replacement**, not a workstation disk or 1B. Unmount any automounted partitions and ensure the destination is not in use. The following overwrites that entire USB:

```bash
image="$replacement_out/usb-a.img"
sudo blockdev --getsize64 /dev/sda
stat -c %s "$image"
# Continue only if the USB byte count is at least the image byte count.
sudo dd if="$image" of=/dev/sda bs=4M conv=fsync status=progress
sudo blockdev --flushbufs /dev/sda
sudo cmp -n "$(stat -c %s "$image")" "$image" /dev/sda
sudo sgdisk --move-second-header /dev/sda
sudo partprobe /dev/sda
sudo udevadm settle
lsblk -o PATH,SIZE,FSTYPE,PARTUUID,UUID,MODEL,SERIAL /dev/sda
```

Stop on any error. `cmp` must exit successfully (no output); it verifies the written image bytes before `sgdisk` relocates the backup GPT to the physical end of a larger USB. This does not enlarge the workspace partition or change its PARTUUID. Confirm partition 2's PARTUUID and partition 1's FAT UUID match **A in the new manifest**, and partition 2 has no filesystem signature:

```bash
sudo wipefs --no-act /dev/sda2
```

Require no signatures on partition 2. Preserve the new manifest and replacement serial outside the pool, clearly labelled **replacement 1A**; retain the original manifest for 1B and the failed A identities. Do not attach either prepared image as a loop device while its physical copy is connected.

#### 4. Boot 1B and replace the missing mirror member

Safely remove the new USB from the workstation. With failed 1A still disconnected, boot the live system from surviving **1B**, then connect the prepared replacement. Keep 1B connected until shutdown. At the root console (the commands also work with sudo), explicitly select pair 1 because automatic selection requires two existing mirror members:

```bash
sudo workspace-mount 1
sudo zpool status -P level1-workspace-1
sudo zpool status -g level1-workspace-1
lsblk -o PATH,SIZE,FSTYPE,PARTUUID,UUID,MODEL,SERIAL
```

Enter the existing workspace passphrase. If import reports another owner, follow the fenced import procedure above; do not force-import without resolving ownership. Confirm the expected pool and data are present on 1B, and status shows its leaf ONLINE and the missing A leaf unavailable. Compare the path listing with the **original** manifest, then take the missing A leaf's numeric vdev GUID from `zpool status -g`. Do not select the pool, mirror, or surviving B GUID.

Set the following to the verified values; `NEW_PARTUUID` comes from **A in the replacement manifest**, not the original A manifest:

```bash
FAILED_MEMBER_GUID=REPLACE_WITH_MISSING_A_NUMERIC_GUID
NEW_PARTUUID=REPLACE_WITH_NEW_A_ZFS_PARTUUID
new_member="/dev/disk/by-partuuid/$NEW_PARTUUID"
ls -l "$new_member"
sudo blockdev --getsize64 "$new_member"
sudo wipefs --no-act "$new_member"
```

Resolve that symlink to the replacement's partition 2 and recheck its serial. Require an empty partition and sufficient partition size (compare with surviving B's ZFS partition for the original equal-size layout). Then replace the failed leaf:

```bash
# MUTATES only the verified new ZFS partition.
sudo zpool replace level1-workspace-1 "$FAILED_MEMBER_GUID" "$new_member"
sudo zpool wait -t resilver level1-workspace-1
sudo zpool status -P level1-workspace-1
```

Do not add `-f` to bypass an unexpected refusal. Investigate the selected disk, size and signatures instead. Never use `zpool create` on the survivor or `zpool add` to replace a mirror member.

Require a completed resilver, **both leaves ONLINE**, zero READ/WRITE/CKSUM errors and no known data errors. Waiting alone is insufficient: `zpool wait` also returns if no resilver is running. If these checks fail, keep 1B authoritative and investigate before relying on the new drive.

#### 5. Save the new membership and test each drive

Save the replacement manifest, serial, new A PARTUUID/FAT UUID, unchanged B identities, pool GUID and final `zpool status -gP level1-workspace-1` output outside the pool. The original initialized images and manifest no longer describe the current A member; never write those images back over the recovered pair.

Close all workspace applications and shells, then export and shut down:

```bash
cd /
sudo workspace-umount 1
sudo poweroff
```

With the machine off, disconnect 1B and test booting **new 1A alone**. Run `sudo workspace-mount 1`, unlock with the existing passphrase, and verify expected files can be read. A degraded mirror is expected with B absent. Export and power off again before removing 1A. If you write to the pool during this test, B becomes stale until it rejoins and resilvers.

Reconnect both drives, boot, run `sudo workspace-mount 1`, wait for any resilver and require both members ONLINE with no errors again. To test 1B alone too, repeat the same export/shutdown/isolate/read-check sequence, then reconnect both and check final mirror health. Label the replacement **1A** and keep independent backups.

When only inspecting a potentially stale original member, first fence every other owner and import by GUID with readonly=on; do not run the normal read-write mount helper on the stale member. Never add `-F`, `-X` or `-T` rewind/recovery flags as a routine fix.

## OS updates and limitations

Follow [OS upgrades](UPGRADES.md) to replace only the FAT live payload. That runbook covers preparation, disk identification, reports and repairing interrupted copies. Whole-image `write-usb` is for new pairs and overwrites workspace data.

- The running root resets; FAT/SquashFS stored on USB are not physically write-protected or cryptographically verified.
- Live changes and the ZFS ARC consume RAM; test the minimum hardware configuration.
- Prefer reliable USB SSDs over low-end flash sticks and keep independent backups.
- Each assembled pair has its own identities. Initialized images are not generic replacement-member templates.
- Signing EFI/kernel/DKMS for Secure Boot, remote authentication and automated replacement-drive provisioning remain outside this workflow.

## Whole-pair recovery

Use [fenced activation](PAIRS.md#fail-over-to-the-next-numbered-pair), then [rebind surviving replicas](PAIRS.md#continue-replication-after-failover). Numeric priority is not snapshot freshness: inspect surviving checkpoints before selecting the new active pair. Keep a returning former active pair isolated until its history has been examined.

To restore the replica count, [add a fresh pair](../README.md#add-more-pairs) with an unused ID and sync from the current active pair. Do not clone an initialized pair or initialize a second independent active workspace.
