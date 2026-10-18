# Ordered pairs: provisioning, rotation and failover

A deployment has **N independent pairs**, with two independently bootable USBs per pair. Assign positive integer IDs without leading zeros: `1`, `2`, …, `10`, … . IDs are permanent identities and define numeric failover order. They do not encode a primary/backup role; `workspace-10` sorts after `workspace-2` numerically (`sort -V`). There is no configured pair-count limit in the helpers.

| Identity | Pool | Dataset | Possible state |
| --- | --- | --- | --- |
| Pair 1, sticks A/B | `workspace-1` | `workspace-1/data` | Active or replica |
| Pair 2, sticks A/B | `workspace-2` | `workspace-2/data` | Active or replica |
| Pair N, sticks A/B | `workspace-N` | `workspace-N/data` | Active or replica |

Exactly one pair is the writable working copy. Others are read-only replicas. `org.level1vault:pair` and `org.level1vault:state` on each pool's root dataset record its identity and current state. Failover changes state and write permission **in place**; pool names, IDs, encryption keys and existing files remain the same. `zfs promote` is not used: it changes clone ancestry, not these operational states.

`sudo workspace-mount` without an ID selects the only complete connected mirror after reading its offline labels; zero or several complete pairs require an explicit ID. Both distinct members must be available for automatic selection, including for an already imported pool. Explicit IDs retain degraded mounting with one member. Discovery never imports a pool or changes its active/replica state.

Every pair uses `/mnt/workspace`, so only one dataset is mounted there at a time. `sudo workspace-mount 2` selects pair 2 explicitly and asks for the passphrase if its key is unloaded. It is writable only when pair 2 is active. Booting a replica's stick does not automatically activate it.

On the live OS, mounting an active pair also attaches `/mnt/workspace/home/simlun` at `/home/simlun`. The home is part of the same encrypted `data` dataset and is included in every normal checkpoint and replication. Read-only replica inspection leaves the temporary home untouched. Exit all simlun sessions and tmux/background processes before exporting an active home; run export from root in `/`. See [home and sessions](HOME.md).

## Provision N fresh pairs

For a new deployment, build and initialize all pairs together:

```bash
make prepare-pairs N=4
```

Pair 1 starts active; the other pairs are already populated replicas and need no initial sync. Choose `N=1` for only the active pair. The passphrase is collected before building and is never saved in images or manifests. Use the same `BUILD_DIR=...` throughout if overriding the default.

For an existing deployment, add empty replicas from its original ISO using unused IDs:

```bash
make add-pair PAIR=5
# Or prepare several additional empty replicas:
for pair in 6 7 8; do
  make add-pair PAIR="$pair"
done
```

After flashing each additional pair, populate it from the current active pair with `workspace-sync` below.
Each new assembly must use a new output directory: it generates unique GPT, PARTUUID and ESP identities. Each initialization creates a separate mirror and pool GUID. Without `--active`, initialization creates an empty receive target in `replica` state. The first raw receive creates its encrypted data filesystem using the source's encryption key; do not independently create/encrypt that filesystem. A `READY` marker means the pair can be flashed, **not that it contains a completed replica**.

Follow [Writing USB drives](../stages/05-write-usb/README.md) for each pair, choosing its output directory and verified physical USB devices. Label both sticks with numeric pair ID and A/B identity. Keep each `WORKSPACE-INFO.txt` and a record of physical serials/location outside the pools. Never create another pair by cloning initialized images. Do not connect image loops and flashed physical copies of the same pair concurrently.

## Initial replication and periodic rotation

Connect the active pair and the intended destination pairs to one exclusive pool owner. The live OS installs the helpers in `/usr/local/sbin`; on a Linux host you can use `sudo ./runtime/workspace-sync ...` directly from the checkout with native ZFS and Python 3.

Sources and destinations are explicit. One command can update any number of destinations:

```bash
sudo workspace-sync 1 2 3 4
```

This creates **one source snapshot checkpoint** and sends it to all listed destinations. The first receive is a full raw encrypted stream; later receives are incremental. Every completed destination has the same endpoint snapshot GUID. This shared checkpoint is particularly useful for failover: a newly activated pair can continue sending to the others from that common history.

For separate offsite rotations, connect one replica at a time and run, for example:

```bash
sudo workspace-sync 1 2
sudo zpool status workspace-2       # both mirror members ONLINE, no errors
cd /
sudo workspace-umount 2
```

Keep another verified copy offsite while one returns for updating. Record each completed checkpoint/date and location. A replica is current only through its last completed checkpoint. To reuse a named checkpoint when different pairs return separately:

```bash
sudo zfs list -t snapshot -o name,creation -s creation -r workspace-1/data
sudo workspace-sync --snapshot EXACT_CHECKPOINT_NAME 1 3
```

Pass the part after `@` as `EXACT_CHECKPOINT_NAME`. That checkpoint must still exist on the active pair and cannot be older than the destination's latest snapshot. A source checkpoint is crash-consistent; quiesce applications first when they need application-level consistency.

IDs express failover priority, **not freshness**. When rotations happen at different times, pair 3 may contain a newer checkpoint than pair 2. If pair 1 then dies and pair 2 is activated, the helper refuses to roll pair 3 backward or merge divergent histories. Inspect and preserve the newer copy; recover any desired newer files before declaring the selected pair authoritative, or explicitly choose a different surviving ID. Once authority is settled, a destination without the necessary common latest snapshot must be reseeded into a fresh pair. Shared checkpoints reduce this issue; they cannot make a disconnected older copy contain future writes.

## Fail over to the next numbered pair

If the active pair dies, fence/disconnect **both of its sticks and every other copy/owner of that pool**. Absence from a USB listing does not prove it is safe to create another writer. There is no automatic failure detector, quorum or multi-host fencing service.

Attach the surviving pairs you want considered, inspect their pool health and latest completed snapshots, then run:

```bash
# Pair 1 was active and is now fenced/unavailable.
sudo workspace-activate --next 1 --fenced
sudo workspace-mount 2
```

`--next 1` discovers imported/importable numeric pair IDs and selects the lowest visible ID greater than 1. Thus it chooses 2 before 3 or 10; if 2 is lost/unavailable, it selects the next visible ID. It does not wrap around to an earlier, potentially stale former active pair. If the selected pair is blank, has an incomplete receive, is mounted, has inconsistent state or has divergent local writes, activation fails for inspection rather than silently skipping it. The command prints the chosen pool and its last completed snapshot.

You may select an ID directly after deliberate inspection:

```bash
sudo workspace-activate 3 --fenced
sudo workspace-mount 3
```

The `--fenced` flag acknowledges the former active owner has been stopped. An observably active/imported different pair still causes refusal. A candidate can be ONLINE or DEGRADED, allowing recovery from a single surviving bootable stick; inspect errors and restore mirror redundancy promptly. Activation needs a completed encrypted replica, not a fresh replacement pair or a full restore. It changes the selected dataset to writable and records active state; the next mount loads the same encryption passphrase. Repeating a completed activation is harmless. An interrupted `activating` state blocks normal mounting/syncing and can be completed by repeating the activation command after inspecting ownership.

Changes after the selected pair's last completed checkpoint are unavailable on that pair. Verify expected files and checkpoint dates before working. Continue with the same numeric ID for all normal commands; no pool renaming or reflashing is required.

## Continue replication after failover

Existing replicas retain their previous source pool/dataset GUID bindings. Change those deliberately when the active pair changes:

```bash
# Pair 2 is now active; pairs 3 and 4 are still replicas.
sudo workspace-sync --rebind 2 3 4
```

`--rebind` permits a source change only when the destination is an unmounted, read-only replica with no pending receive or local writes, and its **latest snapshot GUID exists in the new source's history**. The raw incremental receive also enforces ZFS's encrypted stream ancestry. The target dataset is preserved; there is no forced receive, rollback or snapshot deletion. Subsequent syncs use `sudo workspace-sync 2 3 4` normally.

If pair 2 later fails, fence it and run `sudo workspace-activate --next 2 --fenced`; pair 3 becomes active when it is the next available complete pair. Then explicitly rebind later replicas to source 3. Names and IDs stay stable through every failover.

A repaired former active pair must stay fenced until its history is inspected. Do not mount it writable or automatically prefer its lower ID. If its last state exactly matches a common checkpoint, with **no unreplicated writes**, you can import it without mounting, set `readonly=on` on its data and `org.level1vault:state=replica` on its root, then use `workspace-sync --rebind NEW_ACTIVE_ID RETURNED_ID`. The helper still verifies common history and zero writes using ZFS's strict `written` accounting, not a file-name comparison. If it diverged, preserve its data and reseed a fresh target; never force it over the new active history. Earlier IDs can remain replicas; `--next` deliberately does not wrap around.

For a lost pair, assemble and initialize fresh sticks with a new unused number, sync the current active pair into it, then verify it. Reuse an old ID only after all old physical/image copies with that ID are retired and disconnected. Rebuilding replicas one at a time preserves the other offsite copies.

## Unlock, read and verify a replica

Free `/mnt/workspace` by unmounting/exporting the active pair's dataset, then:

```bash
cd /
sudo workspace-umount 1
sudo workspace-mount 2
stat -c '%u:%g %a' /mnt/workspace   # 2000:2000 750 for a new workspace root
# Read expected files and compare checksums. Writes to a replica should fail.
sudo zpool scrub workspace-2
sudo zpool wait -t scrub workspace-2
sudo zpool status workspace-2
sudo workspace-umount 2
```

Use the IDs currently appropriate to your deployment. Periodically unlock/read each replica and boot each stick alone. Scrubbing does not need a key, but an actual unlock verifies the retained passphrase. Mirror resilience and replication cover different failure modes.

## Interrupted transfers, retention and safe removal

Receives use `-u -s`, remain unmounted/read-only, and never use `-F`. Raw streams do not require the source key to be loaded and preserve encryption keys/passphrases on replicas. The helper unloads a previously loaded destination key before receiving. It refuses mounted destinations, changed roles/properties, unexpected child filesystems/zvols, local writes or divergent latest snapshot history.

After interruption, reconnect the same authoritative source/destination pairs and rerun the same sync command. It resumes each recorded pending endpoint, then catches up to the command's shared checkpoint. A partial first receive cannot be mounted or activated. After loss of the source, automatic `--rebind` cannot adopt an interrupted receive; retain any completed older replica, inspect the pending state and provision a fresh destination if resumption is impossible. No receive is silently aborted to make failover work.

Each destination gets independent `level1vault-<destination-pool-guid>` holds on its last common snapshot. Pending snapshots are held before transfer. Updating one pair cannot release another pair's base. Success moves only the relevant destination's holds; **no snapshots are automatically deleted**. Old holds on a retired source do not move remotely after failover. Retain them until the old history/owner is deliberately retired.

Inspect free space, snapshots and holds periodically. Prune only deliberately selected, unheld old snapshots after accounting for every offsite copy. Do not force-destroy snapshots, release live helper holds, clear source bindings or use `zfs receive -A` as routine retries. A full destination may need capacity/retention work or reseeding while another verified copy remains intact.

All mount, sync, activation and export helpers share a lock. Sync leaves pools imported. Before poweroff or moving sticks:

```bash
cd /
sudo workspace-umount              # sync/export all imported numbered pairs
# Or sudo workspace-umount 2 for just that pair.
sudo poweroff
```

Export validates the pair identity, never imports pools and never forces a busy export. Close applications and leave the workspace in every shell before retrying a failure. Do not remove a running boot stick while SquashFS is still being read from it.

## Encryption and compatibility

Encryption is native ZFS AES-256-GCM on each data filesystem, not whole-device encryption: boot files, pool/snapshot names and some ZFS metadata remain visible. ZFS reads creation/unlock passphrases from stdin; no key file is saved. A terminal hides entry and confirms a new passphrase; redirected stdin supplies one line. The live login password `password` is independent of the workspace passphrase.

Pools enable LZ4, encryption and encryption's required dependencies under `zpool create -d`, all supported by OpenZFS 2.1 on Ubuntu 22.04. Do not enable all newer Debian features if interoperability with OpenZFS 2.1 hosts is required. Raw replicas retain the encryption keys; avoid independent replica passphrase changes. After a deliberate active-pair passphrase change, sync and unlock-test every offsite copy, retaining old passphrases until those copies are updated.

Upstream references: [send](https://openzfs.github.io/openzfs-docs/man/v2.1/8/zfs-send.8.html), [receive](https://openzfs.github.io/openzfs-docs/man/v2.1/8/zfs-receive.8.html), [key handling](https://openzfs.github.io/openzfs-docs/man/v2.1/8/zfs-load-key.8.html).

