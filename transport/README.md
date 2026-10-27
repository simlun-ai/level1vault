# Offline file transport

Put any files to transfer here, then connect **both members** of an exported USB
pair to your Linux workstation with working ZFS:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
make stage-transport PAIR=1
```

Select the pair ID printed on the drives. `PAIR` is mandatory. The command asks
before replacing an existing delivery, copies **all contents of this directory**
(including this README and hidden files), and exports the pool. Local files stay
here. Only this README is tracked by Git; all other contents are ignored.

Boot level1vault offline with the pair connected, then:

```bash
start 1
sudo workspace-transport-import 1
ls /mnt/workspace/inbox/
```

Each delivery gets its own dated directory beneath `inbox/` in the encrypted data
filesystem, owned by simlun. The importer verifies SHA-256 hashes **from the inbox
copy** before removing the unencrypted transport dataset. The pair must be active
and mounted writable; replicas can carry transport but cannot import it until
explicitly activated. Without an ID, import requires exactly one complete pair.

**Transport is unencrypted.** Use it only for data acceptable on unencrypted
media, or encrypt files before staging. Deleting transport is not secure erasure.
Hashes detect corruption, not a malicious workstation changing both files and
manifest. The workstation never receives the workspace encryption key, but it
still has write access to the pool and boot media. Only attach these drives to a
workstation you trust with their integrity.

See [transport operations and retries](../docs/TRANSPORT.md) for details.
