# Pipeline orchestration

The root Makefile is the public command interface. This directory owns the shared BuildKit graph, builder lifecycle, safe cleanup, and collection of stage-owned test suites.

- `check-host.py` reports build, USB and testing dependencies with apt installation advice and separate Docker/ZFS readiness checks.
- `add-pair.py` assembles and initializes a new empty replica from an existing ISO (`make add-pair PAIR=2`).
- `prepare-upgrade.py` builds an ISO-only inspected release in a new `BUILD_DIR` without accessing USBs (`make prepare-upgrade`).
- `upgrade-os.py` requires an explicit completed `UPGRADE_DIR`, validates the selected existing pair, and invokes the stage 6 updater without building.
- `prepare-pairs.py` collects the passphrase up front and orchestrates one OS build, inspection, N initializations, replica seeding and final verification (`make prepare-pairs N=3`).
- `Dockerfile` connects live-build, assembly, inspection, test, and scratch-export targets.
- `buildkit.sh` manages the dedicated builder and exports; stage implementations stay under `../stages/`.
- `lifecycle.py` locks checkout operations against cleanup and hands BuildKit commands back to the invoking sudo user.
- `clean.py` checks host resources and removes guarded legacy temporary build state; `buildkit.sh clean` removes checkout-owned builders and their state volumes. Both preserve host artifacts and workspace data.
- `lint.sh` checks shell sources; `test.py` collects workstation-safe suites from `tests/`, `../runtime/tests/`, and each stage's `tests/`.

New outputs use `build/<run>/live/`, `pairs/<id>/`, and `validation/<test-run>/`; `BUILD_DIR=build/default` is the default run. Builder metadata and build logs live at the run root. Existing artifact directories are not moved or deleted; use explicit `OUT` with initialization, verification, or QEMU when using older pairs. Old ISO exports can be kept as archives; assembling from them requires placing a copy under a new run's `live/` directory.

See the [stage index](../docs/README.md), [development](../docs/DEVELOPMENT.md), and [BuildKit reference](../docs/BUILDKIT.md). CI builds and inspects artifacts but never launches VMs.
