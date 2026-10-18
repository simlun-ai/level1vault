# Persistent home and working sessions

The live console automatically logs in as root. Root's home and OS changes remain temporary. The `simlun` account (UID/GID 2000, Zsh, passwordless sudo) uses a persistent home after the active encrypted workspace is unlocked. No SSH server or remote root authentication is added.

## Everyday workflow

Run `start` from the root console. It uses the same pair discovery as `workspace-mount`: exactly one complete connected pair is required when no number is supplied. Use `start 1` to select a pair explicitly, including deliberate degraded use. The selected pair must be active and writable; replicas can be inspected manually without starting a working session.

After passphrase entry, the command mounts `/mnt/workspace`, attaches `/mnt/workspace/home/simlun` at `/home/simlun` using a bind mount, and runs `sudo -iu simlun tmux new-session -A -s workspace -c /home/simlun`. The named session is created or reattached in a fresh login environment. Inside tmux, `~` is the persistent home. The configured tmux prefix is Control-a.

Exit every pane to finish the session. When the tmux client returns, the wrapper returns to root and asks:

```text
Run workspace-umount? [Y/n]
Power off? [Y/n]
```

Enter accepts a prompt. Declining export skips the poweroff prompt; declining poweroff leaves the exported system running at the root shell. End-of-input declines. A failed mount, home check, tmux launch or export stops the flow without offering poweroff. Export covers all imported workspace pairs.

Detaching (Control-a, then d) also returns to root, but keeps the tmux session running. Export refuses while simlun has running processes, even if those processes have no open files in the home. Run `start` again to reattach, exit its panes, and stop other simlun sessions/background jobs before exporting. Never use forced or lazy unmounts to bypass a busy home.

## First use, replication and upgrades

On the first active mount, create `home/simlun` inside the existing encrypted `workspace-N/data` dataset, owned by `2000:2000`, mode `0700`. Seed it from the image's immutable `/usr/local/share/level1vault/home-default`, including dotfiles and the full offline repository checkout. Publish the seeded directory only after copying and ownership setup succeed. If a normal home directory already exists, validate its ownership/mode and preserve its contents; never merge defaults over it. Unexpected symlinks, ownership, permissions or mount sources cause refusal for inspection.

Existing USB pairs need only an OS upgrade and their first active mount to initialize this directory. No repartitioning, new dataset or encryption-key change is required. Home files are included in the existing workspace snapshots, raw encrypted replication and failover. An active promoted replica uses its saved home when mounted. Other files already in `/mnt/workspace` stay in place.

Later OS upgrades preserve saved shell configuration and the repository checkout in the persistent home. They do not automatically update either from image defaults. Review and apply new defaults deliberately from `/usr/local/share/level1vault/home-default`; update the saved repository checkout with normal Git operations. The image's `source-commit` marker identifies its OS revision, which may differ from the saved checkout.

## Manual operations and recovery

From root, run `workspace-mount [PAIR_ID]`, then `sudo -iu simlun` for a plain working shell. Exit back to root, change to `/`, and run `workspace-umount [PAIR_ID]` before removing drives. The helper unmounts the home before syncing/exporting its pool and refuses running simlun processes or busy mounts. If a later pool export fails, the home may already be detached; resolve the busy references and retry export, or remount the active pair to reattach the home.

Before unlocking, `/home/simlun` is a temporary fallback home. Do not begin a simlun session there before mounting: home attachment refuses while simlun processes are running. Changes made in that fallback home are not automatically copied to the saved home. Root remains available if the workspace cannot be unlocked or its home needs repair.

Mounting a read-only replica does not initialize or attach its home. Inspect saved files at `/mnt/workspace/home/simlun` from root, then export normally. Host-side workspace helpers run from a checkout do not attach homes; the behavior is enabled only inside an installed live image.
