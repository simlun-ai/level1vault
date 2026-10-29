# Offline file transport

Transport carries arbitrary files from a workstation to the offline live system
without unlocking the workspace on that workstation or networking the live OS.
Use `transport/` at the Git root as the local outbox. Only its README is tracked;
all other entries are ignored. Keep independent copies of important source files.

## Stage on the workstation

Export the pair from its previous owner and stop that owner before connecting
both USB members. Disconnect duplicate images or copies of that pool. Identify
the drives and choose the pair ID from their labels:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
cp ~/some-file transport/
make stage-transport PAIR=1
```

`PAIR` must be explicit. Staging checks the source, requires a complete connected
pair, and asks for confirmation before importing or replacing transport. It
imports by the discovered pool GUID with `-N` and `cachefile=none`, without force,
key loading, data mounting, or pool feature upgrades. The encrypted `data` dataset
must remain locked and unmounted, with consistent active/replica policy. Both
mirror members must be ONLINE without device errors. Export an already imported
pool before staging; this command never takes over an existing import.

The command destroys and recreates **only** `level1-workspace-N/transport`.
Existing transport contents are discarded, even if never imported. It refuses
unexpected children or transport snapshots and never uses recursive destruction.
The new sibling dataset is unencrypted, manually mounted, and has execution,
setuid and device access disabled. A payload-sized quota allows at least 64 MiB
of overhead (20% for larger payloads) and bounds temporary consumption; it does
not reserve space. Insufficient pool space fails staging without changing local
source files. Leave adequate workspace headroom.

Everything under the local directory is copied, including README.md, hidden
files and empty directories. Symlinks are preserved without following them;
devices, sockets, FIFOs and nested mounts are refused. Hard links become separate
files, and sparse files may expand. Ordinary file permission bits are preserved
with owner read/write enabled and setid bits removed. Imported directories use
0700. Original owners, ACLs, extended attributes and timestamps are not preserved;
use an archive as the payload when those attributes matter.

The dataset stores `files/`, a versioned JSON manifest and a `READY` marker.
SHA-256 covers every regular file; the manifest also records file sizes,
directories and symlink targets. Metadata is separate from user files, so any
payload filename is allowed. Source and staged copies are compared before READY
is written. Do not change source files during staging. The command unmounts and
exports before reporting that the pair is safe to disconnect. On failure, check
export status before removing the drives. Rerunning staging recreates an
incomplete dataset; an incomplete package is never imported.

## Import during an offline live session

```bash
start 1
sudo workspace-transport-import 1
ls /mnt/workspace/inbox/
```

The importer requires an already mounted, active writable workspace at
`/mnt/workspace`. It never unlocks data, promotes a replica or executes payload
files. Without a pair ID it uses the same unique-complete-pair selection as
`workspace-mount`; supply an ID when multiple complete pairs are connected.
Staging on a replica is allowed, but importing requires its deliberate activation.

Transport is mounted read-only with noexec, nosuid and nodev. Its manifest and
contents are checked, then files are copied into a private temporary inbox
directory. **The copied files in the encrypted inbox are rehashed** before the
delivery is published as:

```text
/mnt/workspace/inbox/YYYYMMDDTHHMMSSZ-<delivery-id>/
```

The delivery belongs to simlun (1000:1000). Each staging operation has a unique
ID, so deliveries never overwrite files from another delivery. The `inbox`
directory must be a real directory inside the data filesystem, owned by UID 1000
with mode 0700; it is created automatically if absent. Verification receipts are
kept separately in the root-owned `inbox/.transport-receipts/` directory. Retain
receipts while a transport cleanup may need retrying.

The importer flushes the verified data, publishes the directory, flushes again,
and unmounts transport before destroying that exact dataset. Source deletion
never precedes successful inbox verification and durable publication. The pool
stays imported for your session; export normally with `workspace-umount` when done.

## Failures and retries

A failed copy or verification retains transport and any private partial inbox
copy. Rerun the importer to replace the partial copy and retry. If publication
succeeded but cleanup failed, rerunning verifies the existing delivery against
its receipt and finishes cleanup without creating another delivery. If the inbox
copy has since been edited, import refuses deletion; preserve those edits and
inspect the discrepancy before deciding how to handle the original package.

An abrupt shutdown may leave a private `.transport-<id>.partial` directory. The
same delivery's retry replaces it. Restaging creates a new delivery ID; obsolete
partial directories from older failed deliveries can be removed deliberately
after inspecting and preserving anything needed. A completed delivery remains
usable even if its source dataset has already been destroyed before interruption.

Transport operations share the workspace lock with mount, sync, activation,
pruning and export. Manual ZFS commands do not take this lock: do not run them
concurrently. A normal pool export also unmounts transport, and refuses busy
mounts. Raw workspace replication sends only `data`; transport is neither backed
up nor propagated. Once imported, inbox files and receipts participate in normal
encrypted workspace snapshots and replication.

## Security boundary

Transport is plaintext storage, and destruction is **not secure erasure**. Encrypt
sensitive payloads before staging if their contents or Git history must not be
recoverable from the USB outside the unlocked workspace. The manifest detects
corruption; it does not authenticate the workstation or its files.

Leaving `data` locked protects its plaintext, but the workstation can still
modify or destroy pool structures and boot files. This workflow assumes you
trust the workstation with drive integrity. Encryption is per dataset, not for
the entire ZFS partition; normal pool metadata remains visible.

## Git repository payloads

The independent [Git bundle helpers](GIT-BUNDLES.md) prepare full repositories in
`transport/git-bundles/` and restore or update working checkouts from the imported
inbox directory. Transport staging and inbox import remain explicit, separate
commands.
