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
make verify
sudo dd if=build/default/pairs/1/usb-a.img of=/dev/sda bs=4M conv=fsync status=progress
sudo dd if=build/default/pairs/1/usb-b.img of=/dev/sdb bs=4M conv=fsync status=progress
sudo sgdisk -e /dev/sda
sudo sgdisk -e /dev/sdb
sudo partprobe /dev/sda /dev/sdb
```

Label the sticks **1-A** and **1-B**. [Writing details and other output directories →](stages/05-write-usb/README.md)

## Use the live system

Disable Secure Boot and boot either stick through UEFI; connect both for mirror redundancy. The console logs in as `simlun` (password `password`, passwordless sudo).

```bash
sudo workspace-mount 1           # enter the workspace passphrase
cd /mnt/workspace               # your persistent files; owned by simlun
```

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
