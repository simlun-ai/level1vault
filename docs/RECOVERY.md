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

Pair **1** is the active workspace, **1A has died**, and **1B holds the authoritative data**. Use these commands to provision one blank USB as the new **1A** and restore the existing mirror. Encryption, passphrase and pool identity are preserved. The same workflow supports replacing B or another numbered pair.

#### 1. Secure 1B and prepare the replacement on the workstation

Stop all other pool owners, disconnect failed 1A and duplicate physical/image copies, and keep an independent backup if possible. Close workspace applications and shells, run `cd /` and `sudo workspace-umount 1`, then power off before unplugging the boot drive. Keep **1B disconnected during preparation and writing**. These commands cannot establish remote fencing or make a backup for you.

From a clean checkout of this revision on a **Linux amd64 workstation with working ZFS**, run:

```bash
make check-host
make prepare-replacement PAIR=1 MEMBER=A RECOVERY_DIR=build/recovery-1a
```

`BUILD_DIR` (default `build/default`) and `OUT` select the **original initialized pair**, whose `images.json`, `WORKSPACE-INFO.txt`, `READY` and original `usb-a.img` are needed. Include `BUILD_DIR=build/original-batch` or `OUT=/path/to/original/pairs/1` if necessary. Keep the original manifests current if a member was previously replaced; the survivor PARTUUID must describe the actual surviving drive.

`RECOVERY_DIR` must be new. This command builds the current OS with `workspace-replace`, assembles fresh boot identities, checks the replacement partition against the original image's size, preserves copies of the original metadata and embeds a non-secret `member-recovery.json` on the new boot filesystem. It never imports a pool, initializes encryption, or touches USB disks. Preparation uses no passphrase. It creates both A/B boot images but selects only A for this recovery; **do not initialize or write the unused B image**.

Retain `build/recovery-1a/pairs/1/` outside the pool. Custom enlarged layouts that do not fit the standard replacement partition are refused; a physically larger USB alone does not enlarge the image partition. Do not use `make initialize`, `make add-pair`, `make prepare-pairs` or the two-disk `make write-usb` for member replacement.

#### 2. Write only the blank replacement USB

Connect only the blank destination USB and identify its **whole-disk path by model, serial and size**:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
# /dev/sda must be the blank replacement, not 1B or a workstation disk.
make write-replacement PAIR=1 MEMBER=A RECOVERY_DIR=build/recovery-1a DISK=/dev/sda
```

Unmount automounted partitions first. This command overwrites **only the selected USB**. It refuses mounted, imported, non-USB, read-only, undersized and nonblank disks; it also refuses to proceed while either recorded original member is connected. It shares the physical-writer lock with normal provisioning and OS upgrades, verifies the written bytes by readback, relocates the backup GPT, and verifies the fresh partition/FAT identities, empty ZFS partition and embedded recovery record. A successful write saves `write-report.json` alongside the preparation metadata.

Stop on errors. An interrupted write may leave signatures on the destination, so a retry can be refused; inspect it and establish that it really is the disposable replacement before deliberately clearing it. No signatures are erased automatically. Label the verified stick **1A** and safely remove it from the workstation.

#### 3. Boot new 1A, connect 1B and restore the mirror

Boot **the newly prepared 1A**, with Secure Boot disabled, so the new recovery command is available even if 1B contains an older OS. Keep it connected until shutdown. At its root console, connect surviving **1B**, leaving failed 1A and other copies disconnected:

```bash
lsblk -o PATH,SIZE,FSTYPE,PARTUUID,UUID,MODEL,SERIAL
sudo workspace-replace 1 --fenced
```

`--fenced` acknowledges that other owners and failed/duplicate members are stopped and disconnected. The command uses the record on the replacement's read-only live boot medium. It verifies the selected pair and pool GUID, the recorded survivor's labels, the new partition and FAT identities, partition sizes, encryption and active/replica policy. It imports the existing pool without mounting it or forcing ownership, finds the sole missing leaf of the two-member mirror, and runs `zpool replace` against the empty replacement partition. No passphrase is required for resilvering encrypted data.

If normal import reports another owner, follow the fenced import procedure above after resolving ownership, then rerun the command. It never force-imports or force-replaces, creates a pool, adds a top-level vdev, promotes a replica or changes the encryption policy.

The command waits for resilver and requires the expected two leaves **ONLINE**, zero READ/WRITE/CKSUM errors and no known data errors before reporting success. Waiting alone is not accepted as proof. You can rerun it to check a completed replacement; unexpected or interrupted nested replacement trees require inspection rather than another automatic replacement. After an interruption, keep both drives attached and inspect `zpool status -gP level1-workspace-1` before proceeding.

Unlock and read-check the workspace using the existing passphrase:

```bash
sudo workspace-mount 1
sudo zpool status -gP level1-workspace-1
```

#### 4. Save membership and test independent boot

Save the final status, unchanged pool GUID, replacement record, new A PARTUUID/FAT UUID, surviving B identities and physical serials outside the pool. Update your current membership manifest's A entry from `build/recovery-1a/pairs/1/images.json`; retain the original metadata as history. The original initialized images no longer represent current membership and must never be written back over the recovered pair.

Close all workspace applications and shells, then:

```bash
cd /
sudo workspace-umount 1
sudo poweroff
```

With the computer off, disconnect 1B and boot **new 1A alone**. Run `sudo workspace-mount 1`, unlock and verify expected files. Degraded status is expected with B absent. Export and power off before removing the boot drive. Reconnect both, boot, mount pair 1, wait for any resilver and require both members ONLINE with no errors again. Test 1B alone with the same shutdown/isolate/read-check sequence, then reconnect both and check final health. Writes made while either member is absent make it stale until it rejoins and resilvers.

When only inspecting a potentially stale original member, first fence every other owner and import by GUID with readonly=on; do not run the normal read-write mount helper on the stale member. Never add `-F`, `-X` or `-T` rewind/recovery flags as a routine fix.

## OS updates and limitations

Follow [OS upgrades](UPGRADES.md) to replace only the FAT live payload. That runbook covers preparation, disk identification, reports and repairing interrupted copies. Whole-image `write-usb` is for new pairs and overwrites workspace data.

- The running root resets; FAT/SquashFS stored on USB are not physically write-protected or cryptographically verified.
- Live changes and the ZFS ARC consume RAM; test the minimum hardware configuration.
- Prefer reliable USB SSDs over low-end flash sticks and keep independent backups.
- Each assembled pair has its own identities. Initialized images are not generic replacement-member templates.
- Signing EFI/kernel/DKMS for Secure Boot, remote authentication and automatic recovery of custom partition layouts remain outside this workflow.

## Whole-pair recovery

Use [fenced activation](PAIRS.md#fail-over-to-the-next-numbered-pair), then [rebind surviving replicas](PAIRS.md#continue-replication-after-failover). Numeric priority is not snapshot freshness: inspect surviving checkpoints before selecting the new active pair. Keep a returning former active pair isolated until its history has been examined.

To restore the replica count, [add a fresh pair](../README.md#add-more-pairs) with an unused ID and sync from the current active pair. Do not clone an initialized pair or initialize a second independent active workspace.
