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

For a **new replacement USB**, first prepare its boot environment without creating a new pool or overwriting the survivor. Use the assembly script's file-based partition/ESP/standalone-GRUB steps as the design reference, but assign fresh GPT/FAT IDs and its own embedded/menu UUID. There is intentionally no automated replacement builder yet. The replacement ZFS partition must be at least the old member's size. Once it has a new, empty, verified partition:

```bash
# MUTATES the selected replacement partition; double-check serial and PARTUUID.
sudo zpool replace level1-workspace-1 FAILED_MEMBER_GUID /dev/disk/by-partuuid/NEW_PARTUUID
sudo zpool wait -t resilver level1-workspace-1
sudo zpool status -gP level1-workspace-1
```

Do not use `zpool create`, do not dd an initialized old workspace image over a new member, and do not add a top-level vdev (`zpool add`). Require both ONLINE and a clean final status before independent use. Save the new member's identities and test its boot environment alone.

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
