# level1vault

A stateless Debian 13 Live system on pairs of bootable USB drives. Each pair holds an encrypted ZFS mirror: your workspace and home persist, while OS changes reset on reboot. Add numbered pairs for offsite replication and recovery.

## Build

Use a **Linux amd64 workstation with working ZFS**, local rootful Docker with Buildx accessible to your user, and at least 80 GiB free. Debian build tools run inside Docker. [Workstation setup →](docs/BUILD.md)

```bash
git clone https://github.com/simlun-ai/level1vault.git
cd level1vault
make check-host                 # check build, USB and testing dependencies
make prepare-pairs N=1          # prepare pair 1 as the active workspace
# Or: make prepare-pairs N=3     # pair 1 active; pairs 2 and 3 populated replicas
```

Sudo and passphrase prompts happen up front. Preparation builds and inspects the OS, creates unique images, initializes encryption, seeds replicas and exports all pools. Only USB writing remains. Each USB must hold at least 12 GiB (16 GiB recommended). Images are `build/default/pairs/1/usb-a.img` and `usb-b.img`, with other pairs under their own IDs. Optional: [test in QEMU first](docs/QEMU.md).

Use **`BUILD_DIR`** to preserve separate batches or OS versions. New builds refuse existing output directories:

```bash
make prepare-pairs N=1 BUILD_DIR=build/new-batch
```

Pass the same `BUILD_DIR` to later writing, testing and add-pair commands. [Build details and lower-level stages →](docs/BUILD.md)

## Keep records; discard completed builds

**Back up `records/` permanently, outside this checkout and the USB workspace.** Provisioning exports small recovery records there automatically; Git ignores them. `build/` holds disposable images, ISOs, releases and logs. Delete a build only after USB readback, independent boot checks, mirror health and a records backup. Images used as real QEMU workspaces may contain data you must save first.

Existing pairs: run `make save-records PAIR=1 BUILD_DIR=build/original-batch` before deleting their original images. Recovery then needs only `records/` and the surviving USB. See [exact retention rules and replacement record updates](docs/ARTIFACTS.md).

## Write the USB drives

Identify both **whole disks** by model and serial:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
```

**This overwrites both selected disks.** Replace `/dev/sda` and `/dev/sdb` with your verified USB devices. Unmount their partitions and export their pools first. Stop QEMU, detach image loops, and disconnect other copies of the same pair.

```bash
make write-usb PAIR=1 DISK_A=/dev/sda DISK_B=/dev/sdb
```

Include `BUILD_DIR=build/new-batch` if you selected it earlier. Label the sticks **1-A/1-B** and test each boots independently. For a batch, repeat with `PAIR=2`, etc., identifying the disks each time. Writes are verified by USB readback. [Writing details →](stages/05-write-usb/README.md)

## Add more pairs

Reuse the original build directory and ISO; choose an unused numeric ID:

```bash
make add-pair PAIR=2
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
# Identify two NEW destination USB disks; this overwrites both selected disks.
make write-usb PAIR=2 DISK_A=/dev/sda DISK_B=/dev/sdb
```

Include the original `BUILD_DIR=...` in both commands if needed. Label the sticks, retain their manifest/serials, and boot-test each independently. Never clone an initialized pair.

Connect the active pair and both new sticks to the live computer, leaving the replica unmounted. If pair 1 is active:

```bash
sudo workspace-sync 1 2
sudo zpool status level1-workspace-2     # require both members ONLINE, no errors
cd /
sudo workspace-umount 2
```

An added pair is empty until its first sync. [Unlock/read-check the replica](docs/PAIRS.md#unlock-read-and-verify-a-replica) before relying on it. Use the current active ID after failover; see [rotation and pair operations](docs/PAIRS.md).

## Use the live system

Disable Secure Boot and boot either stick through UEFI; connect both for mirror redundancy. The local console logs in as **root**. Start working with:

```bash
start                           # select the only complete connected pair
# Or: start 1                    # explicit selection, including degraded use
```

Enter the workspace passphrase. `start` mounts the active workspace and persistent `/home/simlun`, then opens or reattaches the `workspace` tmux session as simlun (UID/GID 1000). Zsh is the working shell; `vim` aliases Neovim. The full repository and Git history are initially at `~/level1vault`.

Exit every tmux pane to return to root and answer the export and poweroff prompts. Detaching leaves tmux running; use `start` to reattach and finish. Export successfully before removing workspace drives, and keep the boot stick connected until shutdown. [Home and session details →](docs/HOME.md)

The MOTD lists manual `workspace-*` commands. Replicas mount read-only; mounting never promotes them. See [replication, retention and failover](docs/PAIRS.md) and [recovery](docs/RECOVERY.md). Kernel logs remain available through `journalctl -k` or `dmesg`.

## Bring files into the offline workspace

Put files in `transport/`, connect both members of an exported pair to the
workstation, then run `make stage-transport PAIR=1`. This replaces the pair's
**unencrypted** transport dataset without unlocking its workspace. During the next
offline live session, run `sudo workspace-transport-import 1` after `start 1`.
It verifies the copied files in the encrypted inbox before deleting transport.
[Workflow, security boundary and retries →](docs/TRANSPORT.md)

## Prepare and apply an OS upgrade

After changing OS packages or configuration, commit your changes. **No preceding `make build` is needed.** Prepare a release on the host without USB drives:

```bash
release="build/$(date +%F)"
make prepare-upgrade BUILD_DIR="$release"
```

Use a new directory, with a suffix for another release on the same day. Add `FRESH=1` to refresh packages/base images. Then export/unmount the existing pair and identify both disks from a separate workstation OS:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
make upgrade-os UPGRADE_DIR="$release" PAIR=1 DISK_A=/dev/sda DISK_B=/dev/sdb
```

This applies the prepared OS payload and verifies the written files; it never builds. ZFS partitions, identities and boot configuration are preserved. Reuse the release for other pairs, and boot-test each stick afterward. [Upgrade runbook and retry procedure →](docs/UPGRADES.md)

## More commands

```bash
make help                       # command overview
make test                       # local lint and behavior tests
make test-container             # assembly and login tests in Debian containers
make clean                      # remove checkout BuildKit state; retain host artifacts
make cache-clean                # prune unused BuildKit cache
```

[Documentation index →](docs/README.md) · [Development →](docs/DEVELOPMENT.md) · [Validation and acceptance →](docs/VALIDATION.md)
