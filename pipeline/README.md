# Pipeline orchestration

The root Makefile is the public command interface. This directory owns the shared BuildKit graph, builder lifecycle, safe cleanup, and collection of stage-owned test suites.

- `Dockerfile` connects live-build, assembly, inspection, test, and scratch-export targets.
- `buildkit.sh` manages the dedicated builder and exports; stage implementations stay under `../stages/`.
- `clean.py` removes only guarded legacy temporary build state, preserving all artifacts and workspace data.
- `lint.sh` checks shell sources; `test.py` collects workstation-safe suites from `tests/`, `../runtime/tests/`, and each stage's `tests/`.

New outputs use `build/<run>/live/`, `pairs/<id>/`, and `validation/<test-run>/`; `BUILD_DIR=build/default` is the default run. Builder metadata and build logs live at the run root. Existing artifact directories are not moved or deleted; use explicit `OUT` with initialization, verification, or QEMU when using older pairs. Old ISO exports can be kept as archives; assembling from them requires placing a copy under a new run's `live/` directory.

See the [stage index](../docs/README.md), [development](../docs/DEVELOPMENT.md), and [BuildKit reference](../docs/BUILDKIT.md). CI builds and inspects artifacts but never launches VMs.
