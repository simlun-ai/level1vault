# Stage 6: upgrade an existing pair's OS

Input: an inspected OS release, an explicit pair ID, and two identified existing whole USB disks. Output: replaced `/live/` and `/.disk/` files on their FAT/EFI partitions, installed `live/release.json`, and a host-side per-stick completion report. Run on the Linux workstation with the pair exported and both sticks idle.

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
make upgrade-os PAIR=2 DISK_A=/dev/sda DISK_B=/dev/sdb
```

`pipeline/upgrade-os.py` builds or reuses a complete release and calls this stage. `build-release.sh` inspects the ISO and uses `../../common/live_payload.py` to export the same live payload as fresh assembly. `upgrade.py` validates the release, both disks' GPT/USB identities and offline ZFS labels, then mounts only EFI partitions and replaces OS files A then B. The existing FAT UUID, EFI loader, GRUB configuration, GPT, and ZFS partition are preserved. It does not import, unlock, initialize, resize, or upgrade a ZFS pool.

Both OS directories are replaced in place; there is no old OS copy or rollback menu. Files are verified before the installed release record is published, then verified again after unmount/remount. A failed copy is repairable by rerunning the same release. A filesystem error may require deliberate FAT repair first. A failed unmount retains its mountpoint for inspection. If A fails, B is not updated; if B fails, the report records A's completion. No successful physical boot is inferred from file verification.

Tests live in `tests/`; the native ZFS integration suite additionally updates real disposable FAT partitions twice and compares workspace partition bytes and GPT records. `copy_test.py` applies the same release to verified disposable image copies for workstation QEMU acceptance; it is not a physical-disk writer. CI does not boot VMs.

See [the upgrade runbook](../../docs/UPGRADES.md) for release reuse, recovery and workstation testing.
