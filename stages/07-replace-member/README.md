# Stage 7: replace one failed mirror member

Preparation inputs: the durable current membership (`RECORDS_DIR/pairs/PAIR/current.json`, default `records/`), explicit `PAIR` and `MEMBER=A|B`, a clean committed checkout and a new `RECOVERY_DIR`. Preparation builds the current live OS and fresh uninitialized images in `RECOVERY_DIR/pairs/PAIR/`. It saves a durable replacement proposal, embeds `member-recovery.json` into the selected image's FAT filesystem and publishes `REPLACEMENT-READY` only after validation. No pool, loop or USB access is needed.

Writing inputs: the prepared directory and one explicitly selected blank whole USB `DISK`. The writer shares `common/usb_io.py` with stage 5, uses `common/usb_devices.py` for destination checks and takes the same physical-disk lock as stages 5/6. Only GPT/PMBR signatures are allowed on a blank destination; existing filesystem signatures cause refusal. It refuses connected original members, verifies image readback before GPT relocation, then checks the partition/FAT identities and empty data partition. Completion publishes `write-report.json` with the sudo caller's ownership.

```bash
make prepare-replacement PAIR=1 MEMBER=A RECOVERY_DIR=build/recovery-1a
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
make write-replacement PAIR=1 MEMBER=A RECOVERY_DIR=build/recovery-1a DISK=/dev/sda
```

The output is a bootable empty member, never a newly initialized pool. Boot this replacement and run `workspace-replace PAIR --fenced` after connecting the recorded survivor. See the complete [recovery runbook](../../docs/RECOVERY.md#member-recovery) for backups, fencing, completion checks, retained identities and independent boot testing.

Standard 512-byte logical-sector GPT layouts are supported. Enlarged workspace partitions that exceed the new image partition require manual preparation. A partially written USB may need deliberate operator cleanup before a retry; the writer never clears signatures automatically. The live command can wait/check a completed replacement again, but refuses unexpected nested replacement topology rather than guessing how to repair it.

Unit tests mock device access, BuildKit, mounts and ZFS. They do not establish physical USB, boot or real resilver acceptance.

After live success and a file read-check, run the printed `make confirm-replacement ... CONFIRMED=yes` command and back up `records/`. Images and build markers are not recovery inputs. See [retention rules](../../docs/ARTIFACTS.md).
