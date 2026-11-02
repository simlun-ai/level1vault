# Persistent home and working sessions

The live console automatically logs in as root. Root's home and OS changes remain temporary. The `simlun` account (UID/GID 1000, Zsh, passwordless sudo) uses a persistent home after the active encrypted workspace is unlocked. No SSH server or remote root authentication is added.

## Everyday workflow

Run `start` from the root console. It uses the same pair discovery as `workspace-mount`: exactly one complete connected pair is required when no number is supplied. Use `start 1` to select a pair explicitly, including deliberate degraded use. The selected pair must be active and writable; replicas can be inspected manually without starting a working session.

After passphrase entry, the command mounts `/mnt/workspace`, attaches `/mnt/workspace/home/simlun` at `/home/simlun` using a bind mount, and starts a managed tmux server in `level1vault-session.service`. The named `workspace` session is created or reattached through its dedicated socket at `/run/level1vault-session/tmux.sock`. The server runs as simlun, starts login shells, and keeps its processes in one systemd control group. Inside tmux, `~` is the persistent home. The configured tmux prefix is Control-a.

Exit every pane to finish the session. When the tmux client returns, the wrapper returns to root and asks:

```text
Run workspace-umount? [Y/n]
Power off? [Y/n]
```

Enter accepts a prompt. Declining export skips the poweroff prompt; declining poweroff leaves the exported system running at the root shell. End-of-input declines. A failed mount, home check, tmux launch or export stops the flow without offering poweroff. Export covers all imported workspace pairs.

Detaching (Control-a, then d) also returns to root, but keeps the tmux session running. Export refuses while any session remains on the managed tmux server. Run `start` again to reattach and exit all its sessions. After the last session ends, accepting export automatically stops the managed server and its remaining background jobs, including GPG/SSH agents and smartcard helpers started there. Cleanup sends SIGTERM, waits up to five seconds, then sends SIGKILL to survivors. Declining export leaves these processes running; a later `start` reuses the server. A subsequent manual `workspace-umount` performs the same cleanup.

Cleanup covers every background job in the managed control group, including jobs that daemonize, rather than selecting process names. Processes started outside it remain untouched and still block home unmount. This includes manual login shells, independent tmux servers, and separately managed services. Processes deliberately launched in another systemd unit are outside this group. Stop those separately and retry from root; the error shows how to inspect remaining simlun processes. Never use forced or lazy unmounts to bypass a busy home.

## First use, replication and upgrades

On the first active mount, create `home/simlun` inside the existing encrypted `level1-workspace-N/data` dataset, owned by `1000:1000`, mode `0700`. Seed it from the image's immutable `/usr/local/share/level1vault/home-default`, including dotfiles and the full offline repository checkout. Publish the seeded directory only after copying and ownership setup succeed. If a normal home directory already exists, validate its ownership/mode and preserve its contents; never merge defaults over it. Unexpected symlinks, ownership, permissions or mount sources cause refusal for inspection.

The home is a directory in the workspace, not a separate dataset. Home files are included in the existing workspace snapshots, raw encrypted replication and failover. An active promoted replica uses its saved home when mounted. Other files already in `/mnt/workspace` stay in place.

Later OS upgrades preserve saved shell configuration and the repository checkout in the persistent home. They do not automatically update either from image defaults. Review and apply new defaults deliberately from `/usr/local/share/level1vault/home-default`; update the saved repository checkout with normal Git operations. The image's `source-commit` marker identifies its OS revision, which may differ from the saved checkout.

## Manual operations and recovery

From root, run `workspace-mount [PAIR_ID]`, then `sudo -iu simlun` for a plain working shell. Exit back to root, change to `/`, and run `workspace-umount [PAIR_ID]` before removing drives. The helper first verifies the matching home mount, refuses live managed tmux sessions, stops managed background jobs, then unmounts the home before syncing/exporting its pool. Remaining unmanaged simlun processes or busy mounts still cause refusal. Exporting another pair does not stop the active session. If a later pool export fails, the home may already be detached; resolve the busy references and retry export, or remount the active pair to reattach the home.

Before unlocking, `/home/simlun` is a temporary fallback home. Do not begin a simlun session there before mounting: home attachment refuses while simlun processes are running. Changes made in that fallback home are not automatically copied to the saved home. Root remains available if the workspace cannot be unlocked or its home needs repair.

Mounting a read-only replica does not initialize or attach its home. Inspect saved files at `/mnt/workspace/home/simlun` from root, then export normally. Host-side workspace helpers run from a checkout do not attach homes; the behavior is enabled only inside an installed live image.

To check a backup such as pair 2, stay at the root console:

```sh
workspace-mount 2
ls -la /mnt/workspace
ls -la /mnt/workspace/home/simlun
cd /
workspace-umount 2
```

Booting or syncing a replica does not activate it. If you run `start` on a replica,
it leaves the backup mounted read-only and explains how to inspect and export it.
Activation is unnecessary for backup checks; use the explicit fenced activation
procedure in [pair operations](PAIRS.md#fail-over-to-the-next-numbered-pair) only
when deliberately changing the writable working pair.

## Session cleanup validation

The shared QEMU export check exercises detached-session refusal, reusing the same
managed server, daemonized GPG and SSH agents, an additional double-forked process
that ignores SIGTERM, and successful export after the last pane exits. It checks
actual control-group membership and process disappearance. Run `make test-qemu`
on a workstation with a freshly built disposable pair; unit tests mock systemd
and cannot establish these live behaviors. Interactive detach/reattach through
`start` and smartcard use with a physical YubiKey remain operator checks.
