# Three pairs: primary and two offsite backups

Each pair has two independently bootable USBs and its **own** ZFS mirror. The six sticks share the same Debian OS payload, but each has unique GPT/PARTUUID/ESP identities; each mirror has a unique pool GUID. Never produce the backups by cloning initialized primary images.

| Pair label | Pool | Data | Normal use |
| --- | --- | --- | --- |
| primary A/B | `workspace` | `workspace/data` | Writable working copy |
| backup1 A/B | `workspace-backup1` | `workspace-backup1/data` | Read-only replica, location 1 |
| backup2 A/B | `workspace-backup2` | `workspace-backup2/data` | Read-only replica, location 2 |

All datasets use `/mnt/workspace`, so only one is mounted there at a time. Each backup stick boots independently; use `sudo workspace-mount backup1` or `backup2` to unlock that pair for read-only recovery. Booting a backup does not silently promote it or import another pair. The default mount command always selects `primary`.

## Provision the additional pairs

Build the OS once using README sections 1–2. Keep the ISO. Assemble each additional pair from that ISO into a **new directory**, so the assembler generates fresh identities. These commands run from the repository root on the Ubuntu host, after `build-container.sh` has built the toolchain:

```bash
sudo docker run --rm \
  --mount "type=bind,source=$PWD,target=/src,readonly" \
  --mount "type=bind,source=$PWD/build,target=/build" \
  level1vault-builder:trixie bash /src/02-assemble-images.sh \
  /build/live-image-amd64.hybrid.iso /build/backup1
sudo docker run --rm \
  --mount "type=bind,source=$PWD,target=/src,readonly" \
  --mount "type=bind,source=$PWD/build,target=/build" \
  level1vault-builder:trixie bash /src/02-assemble-images.sh \
  /build/live-image-amd64.hybrid.iso /build/backup2

sudo ./03-initialize-workspace.sh "$PWD/build/out" primary   # once, fresh primary only
sudo ./03-initialize-workspace.sh "$PWD/build/backup1" backup1
sudo ./03-initialize-workspace.sh "$PWD/build/backup2" backup2
```

Skip the primary initialization if it already exists. Initialization reads the primary encryption passphrase from stdin through ZFS's own prompt, without writing a key file. Backup initialization creates empty mirrors, with no independently encrypted `data` dataset: the first raw receive creates it using the primary's encryption key. Their `READY` markers mean safe to flash, **not that they already contain a backup**.

Follow README section 5 separately for each pair, selecting its output directory and verified physical devices. Label all six drives with pair and A/B identity. Keep each `WORKSPACE-INFO.txt` outside the pool; record physical serial numbers and offsite location alongside its pool GUID. Disconnect the image loop devices before connecting flashed USBs. Do not attach image copies and physical copies of the same pair concurrently.

## Initial backup and periodic rotation

Run the helpers from the live OS. On an Ubuntu host, use `sudo ./live/workspace-sync ...` from the checkout (Python 3 and native ZFS required); the host does not need the live account to perform a raw transfer. Only the live build installs commands into `/usr/local/sbin`.

Connect primary A/B and backup1 A/B to the **same** machine, with exclusive ownership of both pairs. Close or quiesce applications that need application-consistent snapshots. Run:

```bash
sudo workspace-sync backup1
sudo zpool status workspace-backup1
# Verify both mirror members ONLINE and no read/write/checksum errors.
cd /
sudo workspace-umount backup1
```

The first sync sends a full encrypted snapshot. Later syncs send an incremental stream from that backup's most recent matching snapshot, identified by GUID. Repeat with `backup2` when it returns from its separate location:

```bash
sudo workspace-sync backup2
sudo zpool status workspace-backup2
cd /
sudo workspace-umount backup2
```

Keep one verified backup offsite while updating the other. Record the successful snapshot name/date for each location. A backup is only current through its last completed snapshot, regardless of when its sticks were last plugged in. Each destination must be `ONLINE` before a sync; verify both physical members and pool errors before storing it. A degraded source can still be backed up or recovered, but inspect its health first.

`workspace-sync` imports the selected pools without mounting or force. It can send while the primary is mounted, or while all encryption keys are unloaded. It sends raw encrypted data (`zfs send -w -p`) and never needs a passphrase. Backups retain the source's encryption key/passphrase; they do not have separate passwords. The first transfer binds the destination to the source pool **and dataset** GUIDs. Different source identities, unexpected children, a mounted destination, changed read-only policy, local writes or divergent snapshot history cause failure. The helper supports exactly the `data` filesystem; it refuses child filesystems/zvols rather than silently omitting them.

Receives use `-u -s`, remain unmounted and read-only, and never use `-F`. A previously loaded destination key is unloaded before receiving. Both ends of the pipeline must succeed and the resulting snapshot GUID must match. Pools remain imported afterward; **`sudo workspace-umount` with no arguments exports all three known pools**. Use it before poweroff. `workspace-umount primary|backup1|backup2` exports just that pair, without importing anything. Close files and leave `/mnt/workspace` in every shell; failed exports are not forced.

## Verify a backup by unlocking it

Export the primary (or unmount its data) to free `/mnt/workspace`, then:

```bash
cd /
sudo workspace-umount primary
sudo workspace-mount backup1       # enter the primary workspace passphrase
stat -c '%u:%g %a' /mnt/workspace   # new filesystem root: 2000:2000 750
# Read files and compare expected checksums. Writes should fail.
sudo zpool scrub workspace-backup1
sudo zpool wait -t scrub workspace-backup1
sudo zpool status workspace-backup1
sudo workspace-umount backup1
```

Repeat periodically for each backup and boot each stick alone. Scrubbing does not require an encryption key, but actual unlock/read tests verify that your retained passphrase works. Mirror resilience and send/receive backups address different failure modes.

## Interrupted transfers and retention

After an interrupted receive, reconnect the same authoritative pairs and rerun the **same** `workspace-sync backup1` (or `backup2`) command. It resumes the saved receive token before taking another snapshot. The resumed command completes the earlier snapshot; run it again if you also want changes made since the interruption. A partial first receive is not a usable backup. A nonzero exit must never be treated as a completed rotation.

The helper records pending state on the receiver's root dataset and holds the source snapshot before streaming. Each destination's latest successful common snapshot has its own `level1vault-<destination-pool-guid>` hold on source and receiver. Updating backup1 therefore cannot release backup2's incremental base. A failed transfer keeps both the previous base and pending snapshot protected. Success moves only that destination's holds; **no snapshots are automatically deleted**.

Inspect space and holds regularly:

```bash
sudo zfs list -o name,used,available,usedbysnapshots -r workspace
sudo zfs list -t snapshot -o name,creation,used -s creation -r workspace/data
sudo zfs holds workspace/data@EXACT_SNAPSHOT_NAME
```

Prune only deliberately selected, unheld old snapshots after checking the other offsite pair's state. Do not use recursive/forced destroy or release a helper's live holds to make space. A nearly full backup may be unable to receive an incremental stream while keeping its old snapshots. Stop and resolve capacity/retention rather than forcing rollback. The default images expose about 8 GiB of data space even on larger sticks.

If resumption is impossible because source history was deliberately removed or changed, preserve at least one verified backup and reseed **one destination at a time** into a fresh independently assembled/initialized pair. The helper will not automatically destroy or adopt an existing destination. Do not clear GUID bindings to override divergent histories. `zfs receive -A` discards an interrupted receive and is a manual recovery operation, not an automatic retry step.

## Restore after loss of the primary

For immediate file recovery, boot either backup stick, `sudo workspace-mount backup1`, and copy files out. Keep it read-only. For a new writable primary, provision a fresh primary pair as above, but initialize it as a receive target:

```bash
sudo ./03-initialize-workspace.sh /absolute/path/to/fresh-primary-output primary --receive-only
```

Flash the fresh primary images to verified replacement sticks, disconnect image copies, and connect those sticks together with the chosen backup. Fence/disconnect the lost or former primary and any old cloned copies. On the live system:

```bash
sudo workspace-sync --restore backup1
sudo workspace-mount              # same passphrase; restored primary is writable
# Verify expected files, timestamps, ownership and checksums before using it.
```

Restore sends the backup's latest completed snapshot into the empty primary and refuses to overwrite existing data. It can resume an interruption using the same command. It does not automatically select between differing offsite backups: inspect their snapshot dates and verify content first. Do not use `zfs promote` here; that command concerns clone ancestry, not primary/backup roles.

The restored pool/dataset have new identities. Existing backup bindings intentionally refuse the new primary. Keep the recovery source intact and reseed the **other** backup pair first, verify it, then reseed the recovery-source pair. This preserves a usable backup throughout the transition. Never promote two writable primaries and expect ZFS to merge their histories.

## Encryption and compatibility notes

Encryption is native ZFS AES-256-GCM on `data`, not whole-device encryption: boot files, pool/dataset/snapshot names and some ZFS metadata remain visible. Passphrases are entered on stdin during creation and key loading, never embedded in images/scripts/manifests. On a terminal, ZFS hides entry and confirms a new passphrase; redirected stdin supplies one line. Do not put real passphrases into command arguments, shell history or CI logs. The live login password `password` is independent of the workspace encryption passphrase.

Pools enable LZ4 and encryption (plus ZFS's required dependencies) with `zpool create -d`; all these features exist in OpenZFS 2.1. Encryption's `extensible_dataset` dependency also supports resumable receives. Do not enable all newer Debian pool features if Ubuntu 22.04 interoperability is required. Raw receives preserve encryption keys; avoid changing a replica's key independently. After a deliberate primary passphrase change, sync and explicitly unlock-test every backup, and retain the old passphrase until all offline copies are updated.

See the upstream [send](https://openzfs.github.io/openzfs-docs/man/v2.1/8/zfs-send.8.html), [receive](https://openzfs.github.io/openzfs-docs/man/v2.1/8/zfs-receive.8.html), [encryption](https://openzfs.github.io/openzfs-docs/man/v2.1/8/zfs-load-key.8.html), and [feature](https://openzfs.github.io/openzfs-docs/man/v2.1/7/zpool-features.7.html) documentation.
