# Live runtime commands

These files are installed into `/usr/local/sbin` by stage 1. They are shipped software, independent of host-side build and provisioning stages.

`workspace-mount` unlocks and mounts a selected pair; `workspace-umount` safely syncs and exports. `workspace-sync` replicates encrypted checkpoints, and `workspace-activate` performs explicit fenced failover to another numbered pair.

Behavior tests in `tests/` run with mocked system commands through `make test`. Real cross-stage pool tests live in `../integration-tests/zfs/`. Operational guidance is in [pair operations](../docs/PAIRS.md) and [recovery](../docs/RECOVERY.md).
