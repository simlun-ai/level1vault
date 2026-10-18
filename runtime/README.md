# Live runtime commands

These files are installed into `/usr/local/sbin` by stage 1. They are shipped software, independent of host-side build and provisioning stages.

`start [PAIR_ID]` is the live root-console entrypoint: mount the active workspace and its persistent home, enter a simlun login environment in the named `workspace` tmux session, then offer export and poweroff when the tmux client returns. Export failure prevents the poweroff prompt. EOF declines prompts. See [home/session operations](../docs/HOME.md).

`workspace-home` is an internal Python helper for verified bind mounts, atomic first-use seeding, session checks and nonforced home unmounts. Home attachment is enabled only when the live image marker exists; host-checkout workspace operations retain their existing behavior. The helper must remain beside mount/export/start commands.

`workspace-mount [PAIR_ID]` unlocks and mounts a selected pair. Without an ID, `workspace-discover` reads offline ZFS labels through PARTUUID paths, requires exactly one complete mirror with both distinct recorded members, and returns its number. Already imported mirrors are also found this way. No pool is imported during discovery; zero or multiple complete mirrors require an explicit number. Explicit selection retains degraded mounting and all existing identity, encryption, state and mount-policy checks. Neither discovery nor mounting promotes a replica. `workspace-discover` must remain beside `workspace-mount` when running from a checkout.

`workspace-umount` safely syncs and exports. `workspace-sync` replicates encrypted checkpoints, `workspace-activate` performs explicit fenced failover, and `workspace-prune PAIR --keep N` previews retention on an imported pair (`--apply` deletes eligible project checkpoints). Activation and pruning dispatch through `workspace-sync`, sharing its property helpers and operation lock. See [retention policy](../docs/PAIRS.md#snapshot-cleanup).

Behavior tests in `tests/` run with mocked system commands through `make test`. Real cross-stage pool tests live in `../integration-tests/zfs/`. Operational guidance is in [pair operations](../docs/PAIRS.md) and [recovery](../docs/RECOVERY.md).

