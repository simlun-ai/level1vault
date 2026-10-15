# Development and command reference

[Documentation index](README.md)

Run Make from the repository root. Host operations use `sudo`; tests that only inspect code run as your normal user. Use `SUDO=` when already root. The default target, `make`, displays help.

## Commands and lifecycle

| Command | Result |
| --- | --- |
| `make build` | Build the Trixie toolchain, live ISO and first prepared image pair in a new `build/` |
| `make initialize PAIR=1 ACTIVE=1` | Initialize the first writable encrypted mirror on the host; prompt for its passphrase |
| `make verify` | Require `READY` and verify final image/manifest checksums |
| `make assemble OUT=build/pair-2` | Reuse the existing ISO to assemble a new pair with unique identities |
| `make initialize PAIR=2 OUT=build/pair-2` | Initialize an empty replica; its first receive creates the encrypted data filesystem |
| `make builder` | Create/bootstrap the dedicated BuildKit builder |
| `make test` | ShellCheck, shell syntax checks and Python unit/behavior tests |
| `make test-unit` | Only Python unit/behavior tests |
| `make test-container` | Build the toolchain and test real disk assembly and account/login configuration in separate sandboxed BuildKit stages |
| `make test-qemu PAIR=1` | Boot disposable copies of an initialized active pair and verify reboot, degraded writes, and rejoin; see [QEMU](QEMU.md) |
| `make check-build` | Inspect an existing full build's SquashFS, module, initramfs and prepared artifacts |
| `make test-zfs LEVEL1VAULT_DISPOSABLE_TEST_VM=1` | Real ZFS integration, **only on a disposable VM** |
| `make clean` | Remove legacy build work while preserving artifacts |
| `make cache-clean` | Prune unused cache on the dedicated BuildKit builder |

`BUILD_DIR` defaults to `build`; `OUT` defaults to `$(BUILD_DIR)/out`. Relative paths are relative to the repository root. Override them consistently:

```bash
make build BUILD_DIR=build-next
make initialize BUILD_DIR=build-next PAIR=5 ACTIVE=1
make verify OUT=build-next/out
```

Only set `ACTIVE=1` when provisioning the initial writable pair for a deployment. Additional pairs default to replicas. Do not provision a second independent active pair as a failover shortcut; follow [pair operations](PAIRS.md).

Assembly requires an existing ISO; BuildKit builds or reuses its toolchain stage. `OUT` must be a new directory beneath `BUILD_DIR`. Use a local rootful Docker Engine throughout, as for the full build. `BUILDX_BUILDER` and `DEBIAN_IMAGE` select the dedicated BuildKit instance and Debian base image. See the [BuildKit reference](BUILDKIT.md). For advanced live-build arguments, use `build-container.sh BUILD_DIR ...` directly; see [build reference](BUILD.md#reproducibility).

## Local tests on Linux

Install Make, Python 3 and ShellCheck using your distribution’s package manager. For example, on Ubuntu:

```bash
sudo apt update
sudo apt install -y make python3 shellcheck
make test
```

These behavior tests use temporary files and mock system commands. They need no root, Docker or ZFS module. For the real file-only assembly and login tests, install Docker as described in [build setup](BUILD.md), then run:

```bash
make test-container
```

This builds the same Debian toolchain used by CI. Test stages use copied allowlisted source and no insecure RUN entitlement, host ZFS or loop devices. Login tests modify accounts only inside their disposable build stage. Two actual local exports verify fresh disk identities even with a cached ISO.

The real ZFS suite needs root, loop devices, the native ZFS module/tools, `gdisk`, Python and udev. It creates test pools named `workspace-1`, `workspace-2`, `workspace-3` and `workspace-10` and exercises failure recovery. **Run it in a disposable Linux amd64 VM with working ZFS, not your ZFS workstation. Ubuntu 22.04 is the tested CI environment.** It refuses conflicting imported test pools, but that is not a substitute for isolation.

```bash
# Example setup inside a disposable Ubuntu VM:
sudo apt update
sudo apt install -y make python3 zfsutils-linux gdisk udev
make test-zfs LEVEL1VAULT_DISPOSABLE_TEST_VM=1
```

`make test` never enables or runs this suite or QEMU. No automated test writes physical USB drives.

QEMU testing uses `scripts/test-qemu.py`, the guest checks in `tests/qemu-guest.sh`, and the ordered scenarios in `tests/qemu-scenarios.json`. After changing checks or scenarios, run `python3 scripts/qemu-docs.py` to regenerate [the manual commands](QEMU-CHECKS.md). The normal unit tests reject documentation drift. The generated manual and runner use the same guest checks, not separately maintained copies.

## CI

`.github/workflows/check.yml` uses `make test`, `make test-container` and `make test-zfs` in separate Ubuntu 22.04 jobs. The full-build workflow uses `make build` then `make check-build`, retaining build diagnostics on failure. These are the same entrypoints as local runs.

**CI does not launch VMs.** QEMU boot/recovery acceptance is an explicit workstation operation through `make test-qemu PAIR=N`. CI still lints the guest checks, unit-tests the runner without launching QEMU, and checks that the manual acceptance instructions match the shared test sources. Native file-backed ZFS integration on the disposable CI runner remains separate and does not launch a guest.

`make check-build` expects prepared, uninitialized images. Run it before `make initialize`; later initialization intentionally changes the data partitions and image checksums. Full builds and hardware acceptance remain separate from the fast tests. See [validation](VALIDATION.md).

## Cleanup and rebuilds

Stop the builder and QEMU first. Export any pools backed by the images and detach only their own loop devices. Do not run cleanup concurrently with a build or another process using that directory.

```bash
make clean                       # default build directory
make clean BUILD_DIR=build-next  # a selected build directory
```

Current builds export artifacts only; their working state is in BuildKit. Use `make cache-clean` to prune its unused cache. For legacy builds, cleanup removes only `chroot/`, `cache/`, `config/` and `binary/` inside a recognized build directory. It retains the ISO, every image pair (including initialized workspaces), manifests, logs, checksums and state markers. It never exports pools, detaches loops or deletes Docker images. Docker must be available for checks when there is build work to remove. Cleanup refuses active container binds, mounted filesystems and loop-backed files under that build, as well as unexpected paths or symlinks.

The retained build is an archive of artifacts, not a reusable live-build working directory. The builder still refuses an existing directory, so rebuild with a fresh name:

```bash
make clean
make build BUILD_DIR=build-next FRESH=1
```

There is intentionally no target that deletes initialized image pairs. They can contain the only copy of files written during VM tests. If you later discard an entire archived build, check its data and attachments yourself first. Cleanup also preserves an incomplete `INITIALIZING` marker: resolve any leftover pool/loop state using [build troubleshooting](BUILD.md#initialize-a-pair).

## Repository map

- `Makefile`: everyday host commands and shared CI entrypoints.
- `01-configure-live.sh`, `02-assemble-images.sh`, `03-initialize-workspace.sh`: live configuration, file-only disk assembly and native host ZFS initialization.
- `docker/`, `build-container.sh`: Trixie build environment and host wrapper.
- `live/`: account/ZFS build hooks, package list and runtime workspace commands.
- `scripts/`: Makefile support for container tests/assembly and guarded cleanup.
- `tests/`: mocked behavior, real container and disposable-VM ZFS tests.
- `docs/`: operator and technical reference.
