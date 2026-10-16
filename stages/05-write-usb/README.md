# Stage 5: write USB drives

Input: an initialized pair with `READY`, `SHA256SUMS`, and two explicitly identified whole USB disks. Output: two physical bootable sticks. Run on the Linux host with its native ZFS tools.

[Documentation index](../../docs/README.md)

Use two USB drives at least as large as the images (12 GiB each by default; 16 GiB or larger recommended). Stop VMs using the source images, detach their host loop mappings, and disconnect other flashed copies of the same pair: GPT/PARTUUID/ZFS identities are duplicated by flashing.

First identify the **whole disks** by model, serial and size:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
```

Unmount every mounted partition on the selected USBs, disable any swap on them, and export any pools using them. Only after verifying that `/dev/sda` and `/dev/sdb` are the intended USB drives, run from the repository root:

```bash
# DESTRUCTIVE: replace both example device names with verified whole USB disks.
make write-usb PAIR=1 DISK_A=/dev/sda DISK_B=/dev/sdb
```

Both `DISK_A` and `DISK_B` are mandatory; neither has a default. A disk's `/dev/disk/by-id/` alias is also accepted. `PAIR` defaults to 1; `BUILD_DIR` and `OUT` use the same defaults as the other lifecycle targets. The writer uses sudo through the Makefile; use `SUDO=` if already root. Host tools required: Python 3, `lsblk`, `losetup`, `zpool`, `dd`, `sgdisk`, `sync`, and `partprobe`.

The writer verifies the initialized pair ID and checksums, refuses attached source image loops, then validates **both** destination disks before writing either. It rejects duplicate disks, partitions, non-USB disks, undersized/read-only media, mounted filesystems/swap, active device holders and imported ZFS pool members. It does not unmount, export, or stop other users of devices for you. Keep images and disks idle throughout the write; these checks do not prevent another process from opening a disk afterward.

The target writes image A to `DISK_A` and image B to `DISK_B`, flushes writes, relocates each backup GPT to the end of its physical disk, and refreshes the kernel partition tables. The data partition remains approximately 8 GiB; unused physical capacity stays unallocated. GPT relocation does not resize partitions. No write is atomic: if any command fails, stop and identify/inspect both disks again before retrying.

For another freshly assembled and initialized pair, reuse the existing ISO and specify its ID:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
make write-usb PAIR=2 DISK_A=/dev/sda DISK_B=/dev/sdb
```

Use a different, verified pair of physical destination disks. Record pair ID, A/B labels, and serials; test each stick boots independently and follow the [hardware acceptance checks](../../docs/VALIDATION.md). Whole-image writes are for new pairs, never OS updates to a pair containing valuable data.

`make test` includes mocked command-order and refusal tests; it does not write physical disks.
