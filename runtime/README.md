# Live runtime commands

These files are installed into `/usr/local/sbin` by stage 1. They are shipped software, independent of host-side build and provisioning stages.

`workspace-mount [PAIR_ID]` unlocks and mounts a selected pair. Without an ID, `workspace-discover` reads offline ZFS labels through PARTUUID paths, requires exactly one complete mirror with both distinct recorded members, and returns its number. Already imported mirrors are also found this way. No pool is imported during discovery; zero or multiple complete mirrors require an explicit number. Explicit selection retains degraded mounting and all existing identity, encryption, state and mount-policy checks. Neither discovery nor mounting promotes a replica. `workspace-discover` must remain beside `workspace-mount` when running from a checkout.

`workspace-umount` safely syncs and exports. `workspace-sync` replicates encrypted checkpoints, and `workspace-activate` performs explicit fenced failover to another numbered pair.

Behavior tests in `tests/` run with mocked system commands through `make test`. Real cross-stage pool tests live in `../integration-tests/zfs/`. Operational guidance is in [pair operations](../docs/PAIRS.md) and [recovery](../docs/RECOVERY.md).
