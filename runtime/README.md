# Live runtime commands

These files are installed into `/usr/local/sbin` by stage 1. They are shipped software, independent of host-side build and provisioning stages.

`workspace-rsync [rsync options...] SOURCE TARGET` copies with sudo and preserves
permissions, modification times, numeric ownership, symbolic and hard links, ACLs,
extended attributes and sparse files where supported. Checksums decide which files
need copying: repeated runs read both sides but skip unchanged file data. Extra
rsync options go before the two paths, which are passed through unchanged.

```bash
workspace-rsync -n /mnt/vault0/ /mnt/workspace/vault0/  # preview
workspace-rsync /mnt/vault0/ /mnt/workspace/vault0/     # copy contents
# Alternatively, copy the directory itself into /mnt/workspace/vault0/:
workspace-rsync /mnt/vault0 /mnt/workspace/
```

A trailing slash on SOURCE copies its contents into TARGET; without it, rsync
copies the directory itself. Mount the source and writable workspace first.
Destination-only files remain by default. **`--delete` deletes destination entries
absent from the source**; preview with
`workspace-rsync -n --delete SOURCE TARGET` before applying it.

`start [PAIR_ID]` is the live root-console entrypoint: mount the active workspace and its persistent home, enter a simlun login environment in the named `workspace` tmux session, then offer export and poweroff when the tmux client returns. Export failure prevents the poweroff prompt. EOF declines prompts. See [home/session operations](../docs/HOME.md).

`workspace-home` is an internal Python helper for verified bind mounts, atomic first-use seeding, session checks and nonforced home unmounts. Home attachment is enabled only when the live image marker exists; host-checkout workspace operations retain their existing behavior. The helper must remain beside mount/export/start commands.

`workspace-mount [PAIR_ID]` unlocks and mounts a selected pair. Without an ID, `workspace-discover` reads offline ZFS labels through PARTUUID paths, requires exactly one complete mirror with both distinct recorded members, and returns its number. Already imported mirrors are also found this way. No pool is imported during discovery; zero or multiple complete mirrors require an explicit number. Explicit selection retains degraded mounting and all existing identity, encryption, state and mount-policy checks. Neither discovery nor mounting promotes a replica. `workspace-discover` must remain beside `workspace-mount` when running from a checkout.

`workspace-umount` safely syncs and exports. `workspace-sync` replicates encrypted checkpoints, `workspace-activate` performs explicit fenced failover, and `workspace-prune PAIR --keep N` previews retention on an imported pair (`--apply` deletes eligible project checkpoints). Activation and pruning dispatch through `workspace-sync`, sharing its property helpers and operation lock. See [retention policy](../docs/PAIRS.md#snapshot-cleanup).

Behavior tests in `tests/` run with mocked system commands through `make test`. Real cross-stage pool tests live in `../integration-tests/zfs/`. Operational guidance is in [pair operations](../docs/PAIRS.md) and [recovery](../docs/RECOVERY.md).


`workspace-replace PAIR --fenced` restores one failed mirror member from the recovery record on a prepared replacement boot drive. Boot the new drive, connect the survivor, and follow [member recovery](../docs/RECOVERY.md#member-recovery). It shares the workspace lock, preserves encryption/state and requires verified final mirror health.


`workspace-transport-import [PAIR]` verifies a staged transport package into a dated
directory in the active encrypted inbox, then destroys transport. Mount the active
workspace first. See [offline transport](../docs/TRANSPORT.md) for host staging,
verification and retry behavior.

`workspace-transport-import-git-bundles DIRECTORY` runs as simlun after normal
transport import. It restores full Git bundles into `/mnt/workspace/git/` and
fast-forwards clean managed default-branch checkouts. See
[offline Git updates](../docs/GIT-BUNDLES.md) for update policies and limitations.
