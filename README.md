# level1vault

A stateless Debian 13 Live system on pairs of bootable USB drives. Each pair holds an encrypted ZFS mirror; your files persist in `/mnt/workspace`, while OS changes reset on reboot. Add numbered pairs for offsite replication and recovery.

## Build

Use a **Linux amd64 workstation with working ZFS**, local rootful Docker with Buildx, and at least 80 GiB free. Debian build tools run inside Docker. [Setup and build details →](docs/BUILD.md)

```bash
git clone https://github.com/simlun-ai/level1vault.git
cd level1vault
make build
make initialize PAIR=1 ACTIVE=1   # enter a new workspace passphrase
make verify
```

The two ready-to-write images are `build/default/pairs/1/usb-a.img` and `usb-b.img`. Each needs a USB drive of at least 12 GiB (16 GiB recommended). Optional: [test in QEMU first](docs/QEMU.md).

## Write the USB drives

Identify both **whole disks** by model and serial:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
```

**The following overwrites both selected disks.** Replace `/dev/sda` and `/dev/sdb` with your verified USB devices. Unmount their partitions and export their pools first. Stop QEMU and detach any image loops; do not connect other copies of this same pair.

```bash
make write-usb DISK_A=/dev/sda DISK_B=/dev/sdb
```

Label the sticks **1-A** and **1-B**. [Writing details and other output directories →](stages/05-write-usb/README.md)

## Add more pairs

Reuse the existing ISO; there is no need to rebuild the OS for each pair. Choose a new unused numeric ID and output directory. For example, create pair 2 as an empty replica:

```bash
make assemble PAIR=2
make initialize PAIR=2
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
# Identify two NEW destination USB disks; this overwrites both selected disks.
make write-usb PAIR=2 DISK_A=/dev/sda DISK_B=/dev/sdb
```

Use your original `BUILD_DIR` if different from `build/default`. Label the sticks **2-A/2-B**, retain their manifest/serials, and test each boots independently. Each pair needs fresh identities; never clone an existing initialized pair.

Connect the current active pair and both new sticks to one live computer, leaving the replica unmounted. If pair 1 is still active, populate pair 2:

```bash
sudo workspace-sync 1 2
sudo zpool status workspace-2     # require both members ONLINE, no errors
cd /
sudo workspace-umount 2
```

Use the current active ID after a failover. Repeat with IDs 3, 4, and so on; omit `ACTIVE=1` when initializing replicas. `READY` means flashable, not a completed backup: [unlock/read-check the replica](docs/PAIRS.md#unlock-read-and-verify-a-replica) before relying on it. Later, `sudo workspace-sync 1 2 3` updates connected replicas from a shared checkpoint. [Pair operations and rotation →](docs/PAIRS.md)

## Use the live system

Disable Secure Boot and boot either stick through UEFI; connect both for mirror redundancy. The console logs in as `simlun` (password `password`, passwordless sudo).

```bash
sudo workspace-mount 1           # enter the workspace passphrase
cd /mnt/workspace               # your persistent files; owned by simlun
```

The full repository and Git history are available offline at `/home/simlun/level1vault`. Edits there reset on reboot; keep lasting changes in `/mnt/workspace`.

Before poweroff or moving the sticks, close files and leave the workspace in every shell:

```bash
cd /
sudo workspace-umount           # sync and export all imported workspace pairs
sudo poweroff
```

If export fails, resolve the busy filesystem and retry before removing drives. Keep the boot stick connected until shutdown. Never write independently to separated mirror members.

## More commands

```bash
make help                       # command overview
make test                       # local lint and behavior tests
make test-container             # assembly and login tests in Debian containers
make clean                      # clean legacy build work; retain artifacts
make cache-clean                # prune unused BuildKit cache
```

Builds use a fresh directory. To rebuild while retaining existing images: `make build BUILD_DIR=build-next`. Install test dependencies and see the full lifecycle in [development](docs/DEVELOPMENT.md).

[Documentation index →](docs/README.md) · [Additional pairs, sync and failover →](docs/PAIRS.md) · [Recovery →](docs/RECOVERY.md) · [Tested status →](docs/VALIDATION.md)
