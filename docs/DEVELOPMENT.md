# Development and command reference

[Documentation index](README.md)

Run Make from the repository root. Host operations use `sudo`; tests that only inspect code run as your normal user. Use `SUDO=` when already root. The default target, `make`, displays help.

## Commands and lifecycle

| Command | Result |
| --- | --- |
| `make check-host` | Check all build, USB and testing prerequisites and print apt fixes |
| `make prepare-upgrade BUILD_DIR=build/YYYY-MM-DD` | Build and inspect an OS-only release without USB disks |
| `make upgrade-os UPGRADE_DIR=... PAIR=2 DISK_A=... DISK_B=...` | Apply a prepared release to an existing pair; never build |
| `make add-pair PAIR=2` | Assemble and initialize an empty replica using the existing ISO |
| `make prepare-pairs N=3` | Build once and prepare N complete pairs with prompts up front |
| `make build` | Build the Trixie toolchain, live ISO and first prepared image pair in a new `build/default/` |
| `make initialize PAIR=1 ACTIVE=1` | Initialize the first writable encrypted mirror on the host; prompt for its passphrase |
| `make assemble PAIR=2` | Reuse the existing ISO to assemble a new pair with unique identities |
| `make initialize PAIR=2` | Initialize an empty replica; its first receive creates the encrypted data filesystem |
| `make builder` | Create/bootstrap the dedicated BuildKit builder |
| `make ci` | Parallel containerized source, behavior, assembly/account and real host export checks; see [CI architecture](../pipeline/README.md#continuous-integration) |
| `make ci-build` | Full image, inspection, upgrade metadata, ownership and cleanup checks |
| `make ci-zfs LEVEL1VAULT_DISPOSABLE_TEST_VM=1` | Explicitly enabled native ZFS integration on a disposable host |
| `make test` | ShellCheck, shell syntax checks and Python unit/behavior tests |
| `make typecheck` | Pinned Pyright basic type checking targeting Python 3.10 |
| `make lint-python` | Pinned Ruff lint and formatting checks targeting Python 3.10 |
| `make test-unit` | Only Python unit/behavior tests |
| `make test-container` | Build the toolchain and test real disk assembly and account/login configuration in separate sandboxed BuildKit stages |
| `make test-qemu PAIR=1` | Boot disposable copies of an initialized active pair and verify reboot, degraded writes, and rejoin; see [QEMU](QEMU.md) |
| `make check-build` | Inspect an existing full build's SquashFS, module, initramfs and prepared artifacts |
| `make test-zfs LEVEL1VAULT_DISPOSABLE_TEST_VM=1` | Real ZFS integration, **only on a disposable VM** |
| `make clean` | Remove checkout-owned BuildKit builders and cache; preserve exported artifacts |
| `make cache-clean` | Prune unused cache on the dedicated BuildKit builder |

`BUILD_DIR` defaults to `build/default`; `PAIR` defaults to `1`, and `OUT` defaults to `$(BUILD_DIR)/pairs/$(PAIR)`. Relative paths are relative to the repository root. Override them consistently:

```bash
make prepare-pairs N=1 BUILD_DIR=build-next
make add-pair BUILD_DIR=build-next PAIR=5
```

Only set `ACTIVE=1` when provisioning the initial writable pair for a deployment. Additional pairs default to replicas. Do not provision a second independent active pair as a failover shortcut; follow [pair operations](PAIRS.md).

Assembly requires an existing ISO; BuildKit builds or reuses its toolchain stage. `OUT` must be a new directory beneath `BUILD_DIR`. Use a local rootful Docker Engine throughout, as for the full build. `BUILDX_BUILDER` and `DEBIAN_IMAGE` select the dedicated BuildKit instance and Debian base image. See the [BuildKit reference](BUILDKIT.md). For advanced live-build arguments, use `pipeline/buildkit.sh build BUILD_DIR ...` directly; see [build reference](BUILD.md#reproducibility).

## Local tests on Linux

Run `make check-host` to report missing dependencies for every workflow, including package-building unit tests, QEMU firmware/Python modules and disposable ZFS integration. The checker does not run tests or enable privileged integration.

Install Make, Python 3, Debian package tools (`dpkg-deb`) and ShellCheck using your distribution’s package manager. For example, on Ubuntu:

```bash
sudo apt update
sudo apt install -y git make python3 shellcheck dpkg
make test
```

These behavior tests use temporary files and mock system commands. They need no root, Docker or ZFS module. For the real file-only assembly and login tests, install Docker as described in [build setup](BUILD.md), then run:

```bash
make test-container
```

This builds the same Debian toolchain used by CI. Test stages use copied allowlisted source and no insecure RUN entitlement, host ZFS or loop devices. Login tests modify accounts only inside their disposable build stage. Two actual local exports verify fresh disk identities even with a cached ISO.

The real ZFS suite needs root, loop devices, the native ZFS module/tools, `gdisk`, Python and udev. It creates test pools named `level1-workspace-1`, `level1-workspace-2`, `level1-workspace-3` and `level1-workspace-10` and exercises failure recovery. **Run it in a disposable Linux amd64 VM with working ZFS, not your ZFS workstation. Ubuntu 22.04 is the tested CI environment.** It refuses conflicting imported test pools, but that is not a substitute for isolation.

```bash
# Example setup inside a disposable Ubuntu VM:
sudo apt update
sudo apt install -y make python3 zfsutils-linux gdisk udev
make test-zfs LEVEL1VAULT_DISPOSABLE_TEST_VM=1
```

`make test` never enables or runs this suite or QEMU. No automated test writes physical USB drives.

QEMU testing uses `stages/04-test-pair/run.py`, the guest checks in `stages/04-test-pair/guest-checks.sh`, and the ordered scenarios in `stages/04-test-pair/scenarios.json`. After changing checks or scenarios, run `python3 stages/04-test-pair/generate-manual.py` to regenerate [the manual commands](../stages/04-test-pair/README.md). The normal unit tests reject documentation drift. The generated manual and runner use the same guest checks, not separately maintained copies.

## CI

`.github/workflows/check.yml` sets up Buildx and runs `make ci`, with native ZFS isolated in a separate disposable-runner job using `make ci-zfs`. The path-filtered full-build workflow invokes `make ci-build`, retaining diagnostics on failure. The [CI architecture](../pipeline/README.md#continuous-integration) documents Bake targets, caching, source contexts and individual check commands. These are the same entrypoints as local runs.

**CI does not launch VMs.** QEMU boot/recovery acceptance is an explicit workstation operation through `make test-qemu PAIR=N`. CI still lints the guest checks, unit-tests the runner without launching QEMU, and checks that the manual acceptance instructions match the shared test sources. Native file-backed ZFS integration on the disposable CI runner remains separate and does not launch a guest.

`make check-build` expects prepared, uninitialized images. Run it before `make initialize`; later initialization intentionally changes the data partitions. Full builds and hardware acceptance remain separate from the fast tests. See [validation](VALIDATION.md).

## Cleanup and rebuilds

Stop the builder and QEMU first. Export any pools backed by the images and detach only their own loop devices. Do not run cleanup concurrently with a build or another process using that directory.

```bash
make clean                       # default build directory
make clean BUILD_DIR=build-next  # a selected build directory
```

Build working state lives in BuildKit. `make clean` removes only builders recorded as created by this checkout, including their state/cache volumes. It leaves every host artifact untouched: ISOs, initialized images, releases, manifests, logs and markers. The next build recreates its builder. `BUILD_DIR` selects the additional directory checked for in-use resources; it is not a deletion destination.

A checkout-wide exclusive lock rejects cleanup during supported operations. Host checks refuse active container binds, mounted filesystems and loop-backed files under the checkout or selected build. Failed inspection stops cleanup. Docker must be available; cleanup never exports pools, detaches loops, deletes Docker images or runs a global prune. `make cache-clean` instead prunes unused cache on the selected dedicated builder.

The retained build is an archive of artifacts, not a reusable live-build working directory. The builder still refuses an existing directory, so rebuild with a fresh name:

```bash
make clean
make build BUILD_DIR=build-next FRESH=1
```

There is intentionally no target that deletes initialized image pairs. They can contain the only copy of files written during VM tests. If you later discard an entire archived build, check its data and attachments yourself first. Cleanup also preserves an incomplete `INITIALIZING` marker: resolve any leftover pool/loop state using [build troubleshooting](BUILD.md#initialize-a-pair).

## Repository map

| Owner | Responsibility |
| --- | --- |
| `Makefile` | Public lifecycle commands used locally and in CI |
| `pipeline/` | BuildKit graph, builder/export orchestration, cleanup, lint and test collection |
| `stages/01-build-live/` | Live configuration, packages, hooks, ISO build, inspection and tests |
| `stages/02-assemble-pair/` | File-only disk assembly and stage tests |
| `stages/03-initialize-pair/` | Host ZFS initialization, verification and tests |
| `stages/04-test-pair/` | Workstation QEMU runner, shared guest checks, generated manual and tests |
| `stages/05-write-usb/` | Physical USB writer, safety checks, procedure and tests |
| `stages/06-upgrade-os/` | In-place OS updater, release export and refusal/retry tests |
| `common/` | Boot parameters, payload extraction, artifact ownership and USB inspection shared across owners |
| `runtime/` | Shipped workspace commands and behavior tests |
| `integration-tests/zfs/` | Native cross-stage pool integration on explicitly disposable environments |
| `docs/` | Cross-cutting operator and technical references |

Each stage README defines its inputs, outputs, command, failure handling, and tests. See the [stage index](README.md). There is no generic shared scripts directory; orchestration lives in `pipeline/`, while implementation belongs to a stage or the runtime.
