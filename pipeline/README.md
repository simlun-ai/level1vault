# Pipeline orchestration

The root Makefile is the public command interface. This directory owns the shared BuildKit graph, builder lifecycle, safe cleanup, and collection of stage-owned test suites.

- `check-host.py` reports build, USB and testing dependencies with apt installation advice and separate Docker/ZFS readiness checks.
- `add-pair.py` assembles and initializes a new empty replica from an existing ISO (`make add-pair PAIR=2`).
- `prepare-upgrade.py` builds an ISO-only inspected release in a new `BUILD_DIR` without accessing USBs (`make prepare-upgrade`).
- `prepare-pairs.py` collects the passphrase up front and orchestrates one OS build, inspection, N initializations, replica seeding and final verification (`make prepare-pairs N=3`).
- `Dockerfile` connects live-build, assembly, inspection, test, and scratch-export targets.
- `pair-archive.sh` packages sparse images before layer export and extracts prepared pairs as the invoking user.
- `buildkit.sh` manages the dedicated builder and exports; stage implementations stay under `../stages/`.
- `lifecycle.py` locks checkout operations against cleanup and hands BuildKit commands back to the invoking sudo user.
- `clean.py` checks that host resources are idle before cleanup; `buildkit.sh clean` removes checkout-owned builders and their state volumes. Both preserve host artifacts and workspace data.
- `lint.sh` checks shell sources; `test.py` collects workstation-safe suites from `tests/`, `../runtime/tests/`, and each stage's `tests/`.

Outputs use `BUILD_DIR/live/`, `pairs/<id>/`, and `validation/<test-run>/`; `BUILD_DIR=build/default` is the default run. Builder metadata and build logs live at the run root. `make upgrade-os` invokes the stage 6 updater directly; it never builds.

See the [stage index](../docs/README.md), [development](../docs/DEVELOPMENT.md), and [BuildKit reference](../docs/BUILDKIT.md). CI builds and inspects artifacts but never launches VMs.

`make prepare-replacement` and `make write-replacement` expose [single-member recovery](../stages/07-replace-member/README.md); they share lifecycle locking and never initialize a replacement pool.

## Continuous integration

Run `make ci` from the repository root on Linux amd64 with Make, Git, Python 3,
GNU tar and local rootful Docker with Buildx. It needs no host ShellCheck, Python
packages, ZFS or sudo. The existing dedicated checkout builder and lifecycle lock
coordinate checks with `make clean`; no test requests an insecure RUN entitlement.

`docker-bake.hcl` runs these independent checks in parallel:

| Bake target | Check |
| --- | --- |
| `typecheck` | Pyright basic type checking across all Python, targeting Python 3.10 |
| `ruff` | Python lint and formatting, targeting Python 3.10 |
| `shell` | ShellCheck and declared-interpreter syntax checks |
| `unit-pipeline` | Orchestration, export wrapper and lifecycle unit tests |
| `unit-runtime` | Installed command behavior tests |
| `unit-stages` | All stage-owned Python tests, including the QEMU runner without booting guests |
| `assembly` | Actual GPT/FAT/GRUB assembly using a synthetic ISO |
| `account` | Real account, PAM/login and shell configuration in disposable Trixie |

Tool installation is cached, with apt cache mounts for source-check dependencies.
Bake disables execution-stage caching so each check runs on every invocation.
Dependency preparation needs network access; check RUNs disable networking.
Source checks use `ci.Dockerfile` and its separate context allowlist, excluding
host Git metadata, image artifacts, transport payloads and recovery records.
Checkout-dependent unit tests use a synthetic Git repository created inside the
container; no host credentials or history are copied. Production tests continue
to use `Dockerfile` and the production context allowlist.

Most targets use cache-only output, without loading or publishing images.
`ci.sh` retains the assembly fixture in a temporary directory, then runs two
independent pair exports through Bake. It extracts the real sparse archives on
the host and checks sizes, allocation, ownership and unique disk identities.
Bake receives explicit filesystem access only to the temporary ISO input and
export output paths. Temporary outputs are removed on success or failure. `make test-container` runs
only the assembly/account checks and the same complete export verification.

To inspect the graph use `docker buildx bake --print ci`. To run one check,
prepare the project builder with `make builder`, then use:

```sh
docker buildx bake --builder "$(python3 pipeline/lifecycle.py builder-name)" \
  --progress=plain unit-runtime
```

`make test` retains the quick host lint/unit checks, and `python3 pipeline/test.py
pipeline` (or `runtime`, `stages`) selects one owner. Pyright runs in the independent `typecheck` target. Ruff runs as an independent Bake target with its
own pinned tool stage.

GitHub Actions sets up Buildx and calls `make ci`. Native ZFS remains a separate
job invoking `make ci-zfs LEVEL1VAULT_DISPOSABLE_TEST_VM=1`; it still requires an
explicitly disposable host. The path-filtered full build invokes `make ci-build`
to build, inspect, validate upgrade metadata, verify ownership and exercise
cleanup while preserving exported artifacts. Actions retains diagnostic upload
on failure. Neither CI command boots VMs or writes physical USB drives.

## Python lint and formatting

`make lint-python` runs the same pinned Ruff check as the parallel `ruff` target
in `make ci`. Ruff 0.17.2 checks lint and formatting across `pipeline/`, `stages/`,
`runtime/`, `common/` and `integration-tests/`, including extensionless installed
Python commands. `pyproject.toml` targets Python 3.10 with 88-character formatting
and the homelab-hsm rule set (`E4`, `E7`, `E9`, `F`, `I`, `UP`, `B`). This controls
Ruff's compatibility checks; it does not constitute testing on Python 3.10.

The tool stage is isolated from production dependencies and executes checks with
networking disabled. Intentional imports after shared-helper path setup carry
line-specific E402 annotations; other Python lint rules remain enabled.


## Python type checking

`make typecheck` runs the same Pyright target as `make ci`. The check pins Pyright
1.1.414 and Node 24.19.0, matching homelab-hsm, and targets Python 3.10 on Linux.
Its isolated Python 3.10 environment installs only the pinned pexpect dependency
and ptyprocess; it never launches QEMU. Check execution disables networking,
while tool/dependency preparation is cached. Warnings fail the command too.

The initial policy is Pyright **basic**, across all Python source and tests,
including explicit extensionless runtime commands. Unlike homelab-hsm's strict
policy, it permits existing unannotated functions; adopting strict mode requires
a separate annotation migration. There are no baseline ignores or disabled
error rules. Extra import paths resolve shared helpers and upgrade test fixtures.
Dynamic imports and subprocess pipes are explicitly narrowed where needed;
heterogeneous JSON test data is annotated at its boundary. No checker tools or
dependencies are installed in the live image by this target.
