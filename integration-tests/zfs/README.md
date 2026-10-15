# Cross-stage ZFS integration

`run.py` tests stage 3 initialization together with the installed runtime commands: encryption, ownership, replication, interrupted transfers, numeric failover, and preservation of divergent data.

Run only in an explicitly disposable Linux environment with native ZFS:

```bash
make test-zfs LEVEL1VAULT_DISPOSABLE_TEST_VM=1
```

It creates file-backed images and pools; it never launches a VM or writes physical disks. CI runs it directly on its disposable runner. Do not run it on a workstation holding real pools. See [development prerequisites](../../docs/DEVELOPMENT.md).
