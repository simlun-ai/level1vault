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

For additional pairs, periodic `workspace-sync SOURCE_ID TARGET_ID...`, snapshot retention and ordered in-place failover, use [pair operations](PAIRS.md).

For online/replacement procedures and divergent-history restrictions, see [member recovery](#member-recovery). Never run `zpool create` on a surviving member, `zpool add` to replace a mirror member, or `zpool upgrade` just to dismiss the expected feature warning. Do not unplug the running boot medium: SquashFS is still read from it unless explicitly copied to RAM.

## Member recovery

Before every recovery: stop all other pool owners, disconnect duplicate image/physical copies, retain backups, identify authoritative data. Preserve the original build manifest outside the pool.

For a formerly disconnected **original member**, reattach both devices on the authoritative host and import with the mount helper. Inspect:

```bash
sudo zpool status -gP workspace-1
lsblk -o PATH,SIZE,FSTYPE,PARTUUID,UUID,MODEL,SERIAL
ls -l /dev/disk/by-partuuid/
```

If a known member remains unavailable, identify its numeric vdev GUID in status and its matching PARTUUID from images.json. After confirming they are the same original member, try bringing it online:

```bash
sudo zpool online workspace-1 EXISTING_MEMBER_GUID
sudo zpool wait -t resilver workspace-1
sudo zpool status -gP workspace-1
```

If its path cannot be resolved, do not blindly replace it with a stale copy. Inspect labels with `sudo zdb -l /dev/disk/by-partuuid/VERIFIED_PARTUUID`; zdb is supplied by zfsutils-linux. A normal replacement is for a genuinely failed/lost device, not an attempt to reconcile independently written members.

For a **new replacement USB**, first prepare its boot environment without creating a new pool or overwriting the survivor. Use the assembly script's file-based partition/ESP/standalone-GRUB steps as the design reference, but assign fresh GPT/FAT IDs and its own embedded/menu UUID. There is intentionally no automated replacement builder yet. The replacement ZFS partition must be at least the old member's size. Once it has a new, empty, verified partition:

```bash
# MUTATES the selected replacement partition; double-check serial and PARTUUID.
sudo zpool replace workspace-1 FAILED_MEMBER_GUID /dev/disk/by-partuuid/NEW_PARTUUID
sudo zpool wait -t resilver workspace-1
sudo zpool status -gP workspace-1
```

Do not use `zpool create`, do not dd an initialized old workspace image over a new member, and do not add a top-level vdev (`zpool add`). Require both ONLINE and a clean final status before independent use. Save the new member's identities and test its boot environment alone.

When only inspecting a potentially stale original member, first fence every other owner and import by GUID with readonly=on; do not run the normal read-write mount helper on the stale member. Never add `-F`, `-X` or `-T` rewind/recovery flags as a routine fix.

## OS updates and limitations

**Use `make upgrade-os PAIR=N DISK_A=... DISK_B=...` for existing pairs.** It rebuilds/inspects the OS and replaces only the FAT live payload and live-media markers, preserving disk-specific boot files, filesystem/partition identities, and the ZFS partitions. It updates A then B in place, without retaining an old OS copy. An interrupted copy can be repaired by rerunning the same verified release. See [OS upgrades](UPGRADES.md) for requirements, reports, release reuse and recovery. Whole-image `write-usb` remains for new pairs and overwrites workspace data.

- The running root resets; FAT/SquashFS stored on USB are not physically write-protected or cryptographically verified.
- Live changes and the ZFS ARC consume RAM; test the minimum hardware configuration.
- Prefer reliable USB SSDs over low-end flash sticks and keep independent backups.
- Each assembled pair has its own identities. Initialized images are not generic replacement-member templates.
- Signing EFI/kernel/DKMS for Secure Boot, remote authentication and automated replacement-drive provisioning remain outside this workflow.

## Example: fail over with three pairs

Suppose you have three USB pairs:

| Pair | Role | Contents |
| --- | --- | --- |
| 1 | Active | Your working workspace |
| 2 | Replica | Your last sync from pair 1 |
| 3 | Replica | Your last sync from pair 1 |

Pair 1 fails while you are working. Stop its computer and disconnect both sticks, ensuring no other machine or image copy can write to that pool. Then boot using pair 2, with pairs 2 and 3 connected. Inspect the surviving replicas' health and completed checkpoints before choosing one: numeric order does not indicate freshness.

```bash
sudo workspace-activate --next 1 --fenced
```

The arguments mean:

- **`--next 1`** selects the visible pair with the lowest numeric ID greater than 1. Here, that is **pair 2**. If only pair 3 were available, it would select pair 3. It does not wrap around to earlier IDs.
- **`--fenced`** confirms that you have prevented the old active pair from accepting writes. The command does not disconnect or disable pair 1 for you.

Pair 2 becomes active. The command prints the selected pool and its last completed snapshot. Mount the selected pair to resume working:

```bash
sudo workspace-mount 2
```

You recover the data that reached pair 2 during its last successful sync. Changes left only on the failed pair 1 are absent. Verify expected files before continuing.

To make pair 3 follow the new active pair, explicitly rebind it while it is unmounted:

```bash
sudo workspace-sync --rebind 2 3
```

This requires pair 3's latest snapshot to exist in pair 2's history. If pair 3 is newer or divergent, the helper refuses; preserve and inspect that data rather than forcing a rollback. See [replication after failover](PAIRS.md#continue-replication-after-failover).

After a successful rebind, routine updates use:

```bash
sudo workspace-sync 2 3
```

Keep old pair 1 isolated if it returns: it contains the previous active workspace and may have conflicting history. Promoting pair 2 does not make pair 1 safe to reuse.

## Next step: add pair 4 to restore three working pairs

With pair 2 active and pair 3 successfully rebound, replace the lost pair with a fresh, unused ID, **4**. Keep pair 1 fenced.

1. On the Linux build workstation, from the repository root, reuse the existing live ISO and create an **empty replica**, using a new output directory:

   ```bash
   make assemble PAIR=4
   make initialize PAIR=4
   make verify PAIR=4
   ```

   These use `build/default`; use your original `BUILD_DIR` if different. Do not set `ACTIVE=1` or clone an existing pair's initialized images.

2. Follow [Writing USB drives](../stages/05-write-usb/README.md), identifying two new whole disks by serial/model before destructive writes. Run `make write-usb PAIR=4 DISK_A=/dev/sda DISK_B=/dev/sdb`, replacing both example devices with the verified whole disks. The target verifies and writes the pair 4 images. Label the sticks **4A/4B**, save their manifest/serials outside the pools, and test each stick boots independently. Disconnect image loops and duplicate copies before attaching the physical pair.

3. On the live computer owning active pair 2, attach both sticks of pairs 3 and 4. Keep both replicas unmounted, then run:

   ```bash
   sudo workspace-sync 2 3 4
   sudo zpool status workspace-3
   sudo zpool status workspace-4
   ```

   Require successful sync and both members of each mirror ONLINE with no errors. Pair 4 receives its encrypted filesystem and passphrase from pair 2; it needs no `--rebind` because it is new.

4. [Unlock and verify pair 4](PAIRS.md#unlock-read-and-verify-a-replica), substituting active ID **2** and replica ID **4** in that procedure. Check expected files and a clean scrub. Before moving drives, close workspace files, `cd /`, and run `sudo workspace-umount`; keep the running boot USB connected until shutdown.

You now have three working pairs: **2 active, 3 and 4 replicas**. Continue routine replication with `sudo workspace-sync 2 3 4` when all are connected.
