# Cross-stage ZFS integration

`run.py` tests stage 3 initialization together with the installed runtime commands: encryption, ownership, replication, interrupted transfers, numeric failover, and preservation of divergent data.

Run only in an explicitly disposable Linux environment with native ZFS:

```bash
make test-zfs LEVEL1VAULT_DISPOSABLE_TEST_VM=1
```

It creates file-backed images and pools; it never launches a VM or writes physical disks. CI runs it directly on its disposable runner. Do not run it on a workstation holding real pools. See [development prerequisites](../../docs/DEVELOPMENT.md).

The suite also replaces an unavailable member of a real encrypted mirror, verifies the unchanged pool/encryption/state, checks completed-recovery retry and unlocks the restored workspace. Only the USB transport/live-boot inspection is adapted for disposable loop devices; labels, pool import, replacement and resilvering use real ZFS. This does not replace physical boot/USB verification.

Transport checks stage and replace a package on a locked complete mirror, reject
a corrupt package and replica import, verify inbox bytes and ownership, and keep
a replica transport sibling across raw replication.
