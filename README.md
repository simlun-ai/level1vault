# level1vault

A stateless Debian 13 Live system on pairs of bootable USB drives. Each pair holds an encrypted ZFS mirror; your files persist in `/mnt/workspace`, while OS changes reset on reboot. Add numbered pairs for offsite replication and recovery.

## Build

Use a **Linux amd64 workstation with working ZFS**, local rootful Docker with Buildx accessible to your user, and at least 80 GiB free. Debian build tools run inside Docker. [Workstation setup →](docs/BUILD.md)

```bash
git clone https://github.com/simlun-ai/level1vault.git
cd level1vault
make check-host                 # check build, USB and testing dependencies; show apt fixes
```

**Prepare just pair 1, the initial active workspace:**

```bash
make prepare-pairs N=1
```

**Or prepare multiple pairs at once:**

```bash
make prepare-pairs N=3           # pair 1 active; pairs 2 and 3 populated replicas
```

Both commands build and inspect the OS, assemble unique disk images, initialize encrypted workspaces, and export the pools. Sudo and passphrase prompts happen up front. Only USB writing remains; the batch needs no initial sync after flashing. Each USB must hold at least 12 GiB (16 GiB recommended). Optional: [test in QEMU first](docs/QEMU.md).

Images are `build/default/pairs/1/usb-a.img` and `usb-b.img`, with further pairs under `pairs/2/`, etc. Build artifacts and host metadata belong to your invoking user. New builds refuse existing output directories.

**Use `BUILD_DIR` to keep separate batches or OS versions.** It holds that run's ISO, images and reports; choosing another directory preserves earlier images and avoids overwriting a run. For example:

```bash
make prepare-pairs N=1 BUILD_DIR=build/new-batch
make write-usb BUILD_DIR=build/new-batch DISK_A=/dev/sda DISK_B=/dev/sdb
```

Identify the destination disks as described below before writing. Pass the same `BUILD_DIR` to later commands that use that run's artifacts. [Build details and lower-level stages →](docs/BUILD.md)

## Write the USB drives

Identify both **whole disks** by model and serial:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
```

**The following overwrites both selected disks.** Replace `/dev/sda` and `/dev/sdb` with your verified USB devices. Unmount their partitions and export their pools first. Stop QEMU and detach any image loops; do not connect other copies of this same pair.

```bash
make write-usb PAIR=1 DISK_A=/dev/sda DISK_B=/dev/sdb
```

Label the sticks **1-A/1-B**. For a batch, repeat with `PAIR=2`, `PAIR=3`, etc., identifying the destination disks each time. Include `BUILD_DIR=...` if you changed it. [Writing details →](stages/05-write-usb/README.md)

## Add more pairs

Reuse your existing build directory and ISO. Choose an unused numeric ID; one command assembles and initializes an empty replica:

```bash
make add-pair PAIR=2
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
# Identify two NEW destination USB disks; this overwrites both selected disks.
make write-usb PAIR=2 DISK_A=/dev/sda DISK_B=/dev/sdb
```

Include your original `BUILD_DIR=...` in both commands if different from `build/default`. Label the sticks **2-A/2-B**, retain their manifest/serials, and test that each boots independently. Each new pair gets fresh identities; never clone an initialized pair.

Connect the current active pair and both new sticks to one live computer, leaving the replica unmounted. If pair 1 is still active, populate pair 2:

```bash
sudo workspace-sync 1 2
sudo zpool status workspace-2     # require both members ONLINE, no errors
cd /
sudo workspace-umount 2
```

Repeat for IDs 3, 4, and so on, using the current active ID after a failover. An added pair is empty until its first sync: [unlock/read-check the replica](docs/PAIRS.md#unlock-read-and-verify-a-replica) before relying on it. Later, `sudo workspace-sync 1 2 3` updates connected replicas from a shared checkpoint. [Pair operations and rotation →](docs/PAIRS.md)

## Use the live system

Disable Secure Boot and boot either stick through UEFI; connect both for mirror redundancy. The local console automatically logs in as **root**, whose home remains temporary. Begin your working session with:

```bash
start                           # select the only complete connected pair; enter passphrase
# Or: start 1                    # explicitly select pair 1, including degraded use
```

`start` mounts the active workspace, attaches its encrypted persistent home at `/home/simlun`, and opens or reattaches the `workspace` tmux session as `simlun` (UID/GID 1000). Your home, shell settings, history and files survive reboot and replicate with the workspace. Zsh is the working shell; `vim` aliases Neovim. The full repository and Git history are initially available at `~/level1vault`.

Exit every tmux pane to return to root. You will be asked `Run workspace-umount? [Y/n]`, then, after a successful export, `Power off? [Y/n]`. Enter accepts each prompt. Detaching leaves tmux running; run `start` to reattach and finish before exporting. Stop other `simlun` sessions and background jobs too.

For manual mounting, replication and recovery, use the `workspace-*` commands shown in the MOTD and [pair operations](docs/PAIRS.md). Mounting a replica remains read-only and does not attach its home; `start` requires a writable active pair. With several complete pairs connected, specify an ID. Mounting never promotes a replica.

Export successfully before removing workspace drives; keep the boot stick connected until shutdown. Never write independently to separated mirror members. See [persistent home and session details](docs/HOME.md), including first-use defaults and OS upgrades. Kernel messages remain available through `journalctl -k` or `dmesg`.

## Prepare and apply an OS upgrade

After editing OS packages or configuration in this repository, commit your changes. **No preceding `make build` is needed.** First prepare a release on the host, without connecting USB drives:

```bash
release="build/$(date +%F)"        # today's date: YYYY-MM-DD
make prepare-upgrade BUILD_DIR="$release"
```

This builds and inspects the OS and exports an upgrade payload, without creating new workspace pairs. The directory must be new; use a suffix such as `build/2026-10-18-2` for another release on the same day. Add `FRESH=1` to refresh packages/base images instead of reusing the build cache.

Lastly, export/unmount the existing pair, identify both whole disks, and apply the prepared release from a separate workstation OS:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
make upgrade-os UPGRADE_DIR="$release" PAIR=1 DISK_A=/dev/sda DISK_B=/dev/sdb
```

This command **only writes the prepared release**; it never builds. It replaces the FAT OS payload on both sticks and verifies the written files. ZFS partitions, identities, and existing boot configuration are preserved. Repeat this step for other pairs using the same `UPGRADE_DIR`; no rebuild is needed. Boot-test each stick afterward. [Upgrade details, release reuse and recovery →](docs/UPGRADES.md)

## More commands

```bash
make help                       # command overview
make test                       # local lint and behavior tests
make test-container             # assembly and login tests in Debian containers
make clean                      # remove temporary work and BuildKit state; retain artifacts
make cache-clean                # prune unused BuildKit cache
```

[Documentation index →](docs/README.md) · [Development and tests →](docs/DEVELOPMENT.md) · [Recovery →](docs/RECOVERY.md) · [Tested status →](docs/VALIDATION.md)

