# Upgrade the OS on existing USB pairs

[Documentation index](README.md)

## Prepare the release on the host

Run from the repository on your Linux build workstation. Install the usual Docker/Buildx prerequisites and host tools `zfsutils-linux`, `fdisk` (`sfdisk`), `dosfstools` (`fsck.vfat`), and `util-linux`. Keep your host's own ZFS packages and kernel.

Change the repository (for example `stages/01-build-live/packages.list` or a live hook), then commit tracked changes. The shipped full Git checkout must match the OS build revision. Preparation refuses uncommitted or untracked source changes. No preceding `make build` is needed and no USB disks need to be connected:

```bash
release="build/$(date +%F)"
make prepare-upgrade BUILD_DIR="$release"
```

`BUILD_DIR` names the new release directory (for example `build/2026-10-18`). Preparation builds and inspects the ISO in BuildKit and exports an OS-only payload with `UPGRADE-READY`. It never assembles or initializes workspace pairs and runs without host sudo. Existing directories are refused, including incomplete builds; use a new suffix for another release on the same day. Add `FRESH=1` to refresh packages and base images instead of reusing the cache. Build logs and metadata stay with the release. Use `make check-host` to check all build, USB and testing dependencies.

## Apply the prepared release to one USB pair

Stop other users of the selected sticks, export their pool, unmount their filesystems and disable swap on them. Run from a separate workstation OS; the updater refuses a stick that is currently booted/mounted. Identify the **whole disks** by model, serial and size:

```bash
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
# Replace both example devices with the verified disks of the selected pair.
make upgrade-os UPGRADE_DIR="$release" PAIR=2 DISK_A=/dev/sda DISK_B=/dev/sdb
```

`UPGRADE_DIR`, `PAIR`, `DISK_A` and `DISK_B` must be explicitly supplied; stable whole-disk `/dev/disk/by-id/` aliases are accepted. Sudo authentication happens at the start, and the operation runs as one privileged process. No workspace passphrase is requested. Disconnect other physical/image copies of the selected pair and keep both disks idle throughout the operation.

The command requires a complete prepared release and never invokes Docker or builds the OS. It validates exported metadata and paths, inspects both selected disks, then rechecks their identities and usage before writing. It reads offline ZFS labels to verify pair membership and different mirror members, checks partition types/UUIDs, rejects mounted/imported/in-use devices, and checks FAT health and available space.

On each stick, A then B, it mounts only the existing FAT/EFI partition and replaces `/live/` and `/.disk/`. The new files include the matching kernel, initramfs, SquashFS, package list, full repository checkout inside SquashFS, and live-media UUID markers. It records the release, flushes writes, unmounts, invalidates the partition read cache, and verifies the installed files once from a read-only mount against the release hashes.

The existing EFI loader, disk-specific GRUB configuration and kernel command line, FAT UUID, partition table and entire ZFS partition remain in place. Upgrades do not change active/replica roles, keys, datasets, snapshots or pool features. Changes to repository boot-command-line defaults and the standalone EFI loader are outside this payload-only updater. It never runs whole-disk `dd`, formats or resizes a partition, or imports/initializes a workspace pool.

After success, boot each stick independently and check the installed program changes, `uname -r`, and `/usr/local/share/level1vault/source-commit` against the release manifest. Mount the workspace through the usual helper when needed. OS-file verification does not establish a successful physical boot or workspace unlock.

## Reuse a release for other pairs

Keep the prepared directory and select it explicitly for every pair. The release contains its source revision, so deployment does not depend on the workstation checkout still being at that revision. `upgrade-os` never rebuilds or refreshes it.

```bash
release=build/2026-10-18          # use the directory you prepared
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS
make upgrade-os UPGRADE_DIR="$release" PAIR=3 DISK_A=/dev/sda DISK_B=/dev/sdb
```

`UPGRADE_DIR` must contain `UPGRADE-READY` and valid `live/release.json` metadata. `BUILD_DIR`/`OUT` are not inputs to the writing step: an upgrade needs the physical pair and prepared release, not its original initialized images. `DEBIAN_IMAGE`, `BUILDX_BUILDER` and `FRESH` apply during `prepare-upgrade`, not USB writing.

## Failures and retries

The update is in place: there is no retained old OS or rollback menu. Space checks account for the OS files being removed and do not require two complete payloads. Do not unplug disks or interrupt power during copying. An interrupted copy may leave that stick unbootable until the same release is applied again.

Reports are saved at `<release>/reports/pair-<id>-<run>.json`, including release/source revision, disk serials, preserved partition identities, status and the list of completed sticks. Failure on A stops before updating B. Failure on B records A as complete. Fix the reported problem and rerun the same release; both OS copies will be replaced and readback verified again. Workspace contents are not part of the OS repair.

A failed unmount prints and retains its private mountpoint. Resolve that mount before retrying; never recursively remove a directory containing a live mount. If FAT health checks fail after an unclean removal, inspect and repair the **verified EFI partition** deliberately before retrying. The updater does not automatically repair filesystems. A damaged EFI loader/GRUB configuration is also refused rather than guessed.

A failed release build preserves diagnostics but has no `UPGRADE-READY` marker. Do not create that marker manually. Use `make prepare-upgrade BUILD_DIR=...` with a new directory after fixing the build failure; keep incomplete output for diagnosis.

## Workstation QEMU acceptance

Before touching physical disks, build an inspected payload without applying it:

```bash
make prepare-upgrade BUILD_DIR=build/upgrade-test
# Use an initialized active source pair; all mutations below are on disposable copies.
make test-qemu QEMU_ARGS='--upgrade-release build/upgrade-test'
```

The runner checks readiness and copies the original images, replaces only the FAT payload in the disposable copies, checks boot-file preservation, enables the test serial console, and runs the same [boot, reboot, degraded-write and rejoin checks](../stages/04-test-pair/README.md). If you already have a freshly built ISO for the intended revision, `bash pipeline/buildkit.sh release build/default build/default/upgrade-test` exports/inspects a payload from it without rebuilding or transferring workspace images. Both wrapper routes publish `UPGRADE-READY` after inspection and extraction. After the QEMU test passes, select that same directory with `UPGRADE_DIR=...` when upgrading physical pairs; the updater validates metadata and paths before copying and verifies the written USB payload afterward.

The automated upgrade run also checks the booted OS source revision against the release manifest. For a manual QEMU session, compare `/usr/local/share/level1vault/source-commit` to the selected release after login, then use disposable copies with the new payload and follow the [shared manual checks](../stages/04-test-pair/README.md). Keep booting each physical stick independently as a separate acceptance check. CI runs mocked refusal/retry tests, real assembly/extraction, and native disposable FAT/ZFS byte-preservation tests; it never launches a VM.
