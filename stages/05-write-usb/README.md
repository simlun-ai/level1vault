# Stage 5: write USB drives

Input: an initialized pair with verified `READY` and `SHA256SUMS`, plus two explicitly identified whole USB disks. Output: two physical bootable sticks. This stage runs manually on the host; there is no automatic disk writer.

If writing fails, stop and identify the disks again before retrying. After a successful write, test each stick independently and follow the [hardware acceptance checks](../../docs/VALIDATION.md).

[Documentation index](../../docs/README.md)

Use two drives of at least 12 GiB each (16 GiB or larger recommended). Disconnect any loop-mounted image copies and other flashed copies of the same pair: GPT/PARTUUID/ZFS identities are duplicated by flashing.

Identify the **whole disks** by model, serial and size:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
```

Only after verifying that `/dev/sda` and `/dev/sdb` are the intended USB drives, run from the repository root:

```bash
make verify
# DESTRUCTIVE: replace these example device names with verified whole USB disks.
# Unmount every mounted partition on the selected USBs first; export any pools on them.
sudo dd if=build/default/pairs/1/usb-a.img of=/dev/sda bs=4M conv=fsync status=progress
sudo dd if=build/default/pairs/1/usb-b.img of=/dev/sdb bs=4M conv=fsync status=progress
sync
# The image's backup GPT initially lies before the end of a larger real disk.
sudo sgdisk -e /dev/sda
sudo sgdisk -e /dev/sdb
sudo partprobe /dev/sda /dev/sdb
```

The data partition remains approximately 8 GiB; unused physical capacity stays unallocated. Relocating the backup GPT does not resize either partition. Verify each drive boots alone before relying on the pair.

For another pair, use `make verify OUT=build/default/pairs/2` and change both `dd` input paths to that directory. Verify a different pair of physical destination disks. Record the pair ID, A/B labels and serials.
