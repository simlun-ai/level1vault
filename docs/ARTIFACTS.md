# What to keep and what to delete

[Documentation index](README.md)

Generated output has two separate homes under the Git root:

| Folder | Retention | Contents |
| --- | --- | --- |
| `records/` | **Keep permanently and back up separately** | Small, non-secret pair identities and replacement history needed for member recovery |
| `build/` (including a custom `BUILD_DIR` or `RECOVERY_DIR`) | **Disposable after the checks below** | ISOs, disk images, upgrade payloads, build logs and test results |

Both folders are ignored by Git. **A Git commit, push or fresh clone does not back up records.** Back up the entire `records/` directory outside this checkout and outside the workspace pool; keep an independent copy that survives losing the workstation and the active pair. If using `RECORDS_DIR=/another/location`, back up that directory instead and pass the same setting to every provisioning and recovery command.

The encryption passphrase is not in these records. Keep your passphrase separately. Records are not workspace data backups; maintain replicas and independent data backups too.

## Durable records

`make prepare-pairs`, `make add-pair` and `make initialize` export records after successful initialization. `make write-usb` also checks/exports them **before writing**. An export failure means the command failed: preserve the build output, fix the cause and run `make save-records` before deleting anything.

| Path | Meaning |
| --- | --- |
| `records/pairs/1/initial.json` | Initial pool GUID, A/B PARTUUIDs, FAT UUIDs and actual data partition sizes; retained as history |
| `records/pairs/1/current.json` | Current membership used by `make prepare-replacement` |
| `records/pairs/1/replacements/<id>.json` | Proposed replacement, previous/next membership and embedded recovery record; retain even after completion |
| `records/pairs/1/.lock` | Serialization file; not recovery information, harmless to back up |

Replacement preparation records a proposal without changing `current.json`. After `workspace-replace` reports success and you unlock/read-check the files, run the printed command on the workstation:

```bash
make confirm-replacement PAIR=1 REPLACEMENT=THE_PRINTED_UUID CONFIRMED=yes
```

This is your explicit acknowledgement of live completion, not an automatic check of the USBs. It advances only the expected previous membership (or succeeds harmlessly if already confirmed), preserving history. **Back up records again immediately.** Do not confirm a failed or abandoned replacement. Confirm the completed replacement before preparing another one for this pair. Label physical drives; retain serial numbers, final pool status and write/upgrade reports alongside your backup if you want an operational audit trail. Those reports are useful history but are not inputs to member recovery.

Never edit identities to make a mismatched drive pass checks. If a pair was recreated or records disagree with physical membership, inspect the pool and device identities deliberately. A previously used pair ID cannot silently replace its records.

## Existing deployments: export once before deleting images

For each pair whose original initialized images still match its current physical members:

```bash
make save-records PAIR=1 BUILD_DIR=build/original-batch
# Or: make save-records PAIR=1 OUT=/path/to/original/pairs/1
```

Back up `records/`, then use the deletion criteria below. Export reads both images' partition tables once; subsequent replacement preparation needs neither image, `READY`, `WORKSPACE-INFO.txt` nor the old ISO.

If a member was already replaced under the old workflow, **do not export stale original identities as current membership**. Reconcile metadata and partition bounds with the actual replacement/survivor first, retaining the old metadata as history. If the old images/metadata are already gone, there is no automatic reconstruction command in this change; inspect the surviving device and pool rather than guessing identities.

## When build data becomes disposable

- **Initial pair images:** after both USB writes pass readback, each stick boots independently, expected files are checked, the reconnected mirror is healthy, and `records/` is backed up. Never reflash old initialized images over a used pair: that overwrites current workspace data.
- **Replacement images:** after live replacement, file verification, membership confirmation, independent boot checks, final mirror health and a fresh records backup. Keep unfinished recovery output until resolved. If it was deleted after writing, use the printed replacement UUID/history record to confirm completion.
- **Upgrade releases:** after every intended pair is upgraded and each stick boot-tested. Keep the release through failures/retries; it is needed to repair an interrupted copy. Retain it longer only if you want to reuse that exact OS payload.
- **ISOs:** optional caches for `make add-pair`. Deleting one means rebuilding an OS before assembling further pairs; it does not prevent member recovery, which builds the current committed OS.
- **Logs and validation results:** optional diagnostics/audit history after success. Preserve failed-operation output until pools, loop devices, mounts and partial writes have been resolved.

**Exception: an image used as a real workspace in QEMU can contain your only copy of new data.** Export it and back up/sync that data before removing the image. Directory placement alone never proves data is disposable.

Custom build/recovery directories are still disposable staging locations, never substitutes for `records/`. Keep them separate from `RECORDS_DIR`; do not put records underneath a build directory.

`make clean` continues to remove checkout BuildKit state while preserving host files, including records and initialized images. It does not delete `build/`. After meeting the criteria above, exporting pools and detaching image loops, you may remove the selected build directory yourself. No automatic destructive cleanup of workspace images is added.
