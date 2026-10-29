.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
# Serialize lifecycle targets even when invoked with make -j.
.NOTPARALLEL:

# Set the default build and host-operation options.
BUILD_DIR ?= build/default
OUT ?= $(BUILD_DIR)/pairs/$(PAIR)
PAIR ?= 1
ACTIVE ?= 0
BUILDX_BUILDER ?=
DEBIAN_IMAGE ?= debian:trixie
FRESH ?= 0
SUDO ?= sudo
N ?=
UPGRADE_DIR ?=
DISK_A ?=
DISK_B ?=
MEMBER ?=
DISK ?=
RECORDS_DIR ?= records
REPLACEMENT ?=
CONFIRMED ?=
REPO ?=
export REPO
RECOVERY_DIR ?=
export RECORDS_DIR REPLACEMENT CONFIRMED
export SUDO BUILD_DIR OUT PAIR ACTIVE BUILDX_BUILDER DEBIAN_IMAGE FRESH DISK_A DISK_B N UPGRADE_DIR MEMBER DISK RECOVERY_DIR

RUN = python3 pipeline/lifecycle.py shared

# Expose the supported project lifecycle commands.
.PHONY: add-transport-git-bundle typecheck lint-python ci ci-build ci-zfs stage-transport save-records confirm-replacement prepare-replacement write-replacement help check-host add-pair prepare-upgrade prepare-pairs build builder assemble initialize lint test-unit test test-container test-zfs test-qemu write-usb upgrade-os check-build clean cache-clean
# Show the common build, test, and cleanup commands.
help:
	@printf '%s\n' \
	  'make add-transport-git-bundle REPO=<url>  Prepare a full offline Git bundle in transport/git-bundles/' \
	  'make stage-transport PAIR=1       Copy transport/ onto a locked pair, replacing previous transport' \
	  'make save-records PAIR=1         Export recovery records from existing initialized images' \
	  'make confirm-replacement PAIR=1 REPLACEMENT=... CONFIRMED=yes  Record successful live recovery' \
	  'make check-host                  Check build, USB and test dependencies; print apt fixes' \
	  'make prepare-pairs N=1           Build and initialize one active pair' \
	  'make add-pair PAIR=2             Assemble and initialize an empty replica from the existing ISO' \
	  'make prepare-upgrade BUILD_DIR=build/YYYY-MM-DD  Build an inspected OS release without USB disks' \
	  'make prepare-pairs N=3           Prepare one active pair and N-1 populated replicas' \
	  'make build                       Build live ISO and first uninitialized image pair' \
	  'make initialize PAIR=1 ACTIVE=1   Create the first encrypted, writable workspace' \
	  'make assemble PAIR=2              Prepare another pair from the existing ISO' \
	  'make initialize PAIR=2            Initialize an empty replica' \
	  'make write-usb DISK_A=/dev/sda DISK_B=/dev/sdb  Write the selected USB pair (destructive)' \
	  'make upgrade-os UPGRADE_DIR=... PAIR=2 DISK_A=/dev/sda DISK_B=/dev/sdb  Apply a prepared OS release' \
	  'make prepare-replacement PAIR=1 MEMBER=A RECOVERY_DIR=build/recovery-1a  Build one empty replacement' \
	  'make write-replacement PAIR=1 MEMBER=A RECOVERY_DIR=build/recovery-1a DISK=/dev/sda  Write one blank USB' \
	  'make typecheck                   Check Python types targeting Python 3.10' \
	  'make lint-python                 Check Python lint and formatting with pinned Ruff' \
	  'make ci                          Run parallel containerized checks and host export validation' \
	  'make test                        Run local lint and unit/behavior tests' \
	  'make test-container              Test real assembly and login in Trixie' \
	  'make test-qemu                    Test pair 1 in QEMU; choose writable results automatically' \
	  'make test-zfs                     Real ZFS tests; disposable VM only (see docs)' \
	  'make check-build                 Inspect an already-built live system' \
	  'make clean                       Remove checkout BuildKit state; preserve all host artifacts' \
	  'make cache-clean                 Prune unused cache on the dedicated builder' \
	  'Options: BUILD_DIR=build-next, OUT=..., SUDO= (when already root)'

# Report prerequisites without installing packages or changing host state.
check-host:
	@command -v python3 >/dev/null || { echo 'Missing python3; run: sudo apt install -y python3' >&2; exit 1; }
	python3 pipeline/check-host.py

# Build an inspected upgrade release without requiring USB devices.
prepare-upgrade:
	$(RUN) python3 pipeline/prepare-upgrade.py --build-dir "$$BUILD_DIR"

# Assemble and initialize a new empty replica from this run's existing ISO.
add-pair:
	@[[ '$(origin PAIR)' == 'command line' || '$(origin PAIR)' == environment ]] || { echo 'Set PAIR explicitly, e.g. PAIR=2' >&2; exit 2; }
	@[[ "$$PAIR" =~ ^[1-9][0-9]*$$ && "$$ACTIVE" == 0 ]] || { echo 'Use a positive PAIR and ACTIVE=0 for an additional replica' >&2; exit 2; }
	$(RUN) python3 pipeline/add-pair.py --build-dir "$$BUILD_DIR" --out "$$OUT" --pair "$$PAIR"

# Prepare a complete initial set with all authentication and secret prompts upfront.
prepare-pairs:
	@[[ "$$N" =~ ^[1-9][0-9]*$$ ]] || { echo 'Set N to a positive pair count, e.g. N=3' >&2; exit 2; }
	$(RUN) $(SUDO) env RECORDS_DIR="$$RECORDS_DIR" LEVEL1VAULT_LOCKED=1 BUILDX_BUILDER="$$BUILDX_BUILDER" DEBIAN_IMAGE="$$DEBIAN_IMAGE" FRESH="$$FRESH" python3 pipeline/prepare-pairs.py --build-dir "$$BUILD_DIR" --count "$$N"

# Build and export the live system.
build:
	$(RUN) env PAIR="$$PAIR" BUILDX_BUILDER="$$BUILDX_BUILDER" DEBIAN_IMAGE="$$DEBIAN_IMAGE" FRESH="$$FRESH" bash pipeline/buildkit.sh build "$$BUILD_DIR"

# Prepare the project builder.
builder:
	$(RUN) env PAIR="$$PAIR" BUILDX_BUILDER="$$BUILDX_BUILDER" bash pipeline/buildkit.sh builder

# Prepare another independently bootable pair.
assemble:
	$(RUN) env PAIR="$$PAIR" BUILDX_BUILDER="$$BUILDX_BUILDER" DEBIAN_IMAGE="$$DEBIAN_IMAGE" bash pipeline/buildkit.sh assemble "$$BUILD_DIR" "$$OUT"

# Initialize an encrypted active workspace or empty replica.
initialize:
	@[[ "$$PAIR" =~ ^[1-9][0-9]*$$ ]] || { echo 'Set PAIR to a positive integer, e.g. PAIR=1' >&2; exit 1; }
	@[[ "$$ACTIVE" == 0 || "$$ACTIVE" == 1 ]] || { echo 'ACTIVE must be 0 or 1' >&2; exit 1; }
	$(RUN) $(SUDO) ./stages/03-initialize-pair/initialize.sh "$$OUT" "$$PAIR" $$( [[ "$$ACTIVE" == 1 ]] && printf '%s' --active || : )
	$(RUN) python3 pipeline/records.py save --out "$$OUT" --pair "$$PAIR"

# Write an initialized pair to two explicitly identified whole USB disks.
write-usb:
	@[[ -n "$$DISK_A" && -n "$$DISK_B" ]] || { echo 'Set both DISK_A and DISK_B; identify disks with lsblk first' >&2; exit 2; }
	$(RUN) python3 pipeline/records.py save --out "$$OUT" --pair "$$PAIR"
	$(RUN) $(SUDO) python3 stages/05-write-usb/write.py --out "$$OUT" --pair "$$PAIR" --disk-a "$$DISK_A" --disk-b "$$DISK_B"

# Apply a prepared OS release to one explicitly identified existing pair in place.
upgrade-os:
	@[[ -n "$$UPGRADE_DIR" ]] || { echo 'Set UPGRADE_DIR to a completed make prepare-upgrade release' >&2; exit 2; }
	@[[ '$(origin PAIR)' == 'command line' || '$(origin PAIR)' == environment ]] || { echo 'Set PAIR explicitly for an OS upgrade' >&2; exit 2; }
	@[[ "$$PAIR" =~ ^[1-9][0-9]*$$ && -n "$$DISK_A" && -n "$$DISK_B" ]] || { echo 'Set PAIR, DISK_A and DISK_B; identify disks with lsblk first' >&2; exit 2; }
	$(RUN) $(SUDO) python3 stages/06-upgrade-os/upgrade.py --pair "$$PAIR" --disk-a "$$DISK_A" --disk-b "$$DISK_B" --release "$$UPGRADE_DIR"

# Check shell scripts for syntax and lint errors.
lint:
	bash pipeline/lint.sh

# These tests mock host mutations; no sudo, Docker or loaded ZFS module needed.
test-unit:
	python3 pipeline/test.py

# Run the workstation-safe checks.
test: lint test-unit

# Check Python types using the pinned container toolchain.
typecheck:
	$(RUN) bash pipeline/buildkit.sh typecheck

# Check Python lint and formatting with the same pinned Ruff as CI.
lint-python:
	$(RUN) bash pipeline/buildkit.sh ruff

# Run workstation-safe CI through Bake, keeping cleanup excluded throughout.
ci:
	$(RUN) bash pipeline/buildkit.sh ci

# Exercise real image and cleanup contracts; clean acquires its own exclusive lock.
ci-build:
	bash pipeline/ci-build.sh

# Preserve the explicit disposable-environment gate on native ZFS integration.
ci-zfs: test-zfs

# Verify image assembly and account setup in Debian.
test-container:
	$(RUN) env PAIR="$$PAIR" BUILDX_BUILDER="$$BUILDX_BUILDER" DEBIAN_IMAGE="$$DEBIAN_IMAGE" bash pipeline/buildkit.sh test

# Verify boot and recovery with automatic results storage (PAIR defaults to 1).
test-qemu:
	$(RUN) python3 stages/04-test-pair/run.py --out "$$OUT" --pair "$$PAIR" $(QEMU_ARGS)

# Deliberately excluded from `make test`: do not opt a workstation into this.
test-zfs:
	@test "$${LEVEL1VAULT_DISPOSABLE_TEST_VM:-}" = 1 || { echo 'Use a disposable VM and set LEVEL1VAULT_DISPOSABLE_TEST_VM=1' >&2; exit 1; }
	$(SUDO) env LEVEL1VAULT_DISPOSABLE_TEST_VM=1 python3 integration-tests/zfs/run.py

# Inspect the completed live build.
check-build:
	$(RUN) env PAIR="$$PAIR" BUILDX_BUILDER="$$BUILDX_BUILDER" DEBIAN_IMAGE="$$DEBIAN_IMAGE" bash pipeline/buildkit.sh check "$$BUILD_DIR" "$$OUT"

# Remove checkout-owned Docker state while preserving all host artifacts.
clean:
	python3 pipeline/lifecycle.py clean "$$BUILD_DIR"

# Prune unused project build cache.
cache-clean:
	$(RUN) env PAIR="$$PAIR" BUILDX_BUILDER="$$BUILDX_BUILDER" bash pipeline/buildkit.sh cache-clean


# Prepare one replacement without initializing or modifying the original pair.
prepare-replacement:
	@[[ "$$PAIR" =~ ^[1-9][0-9]*$$ && "$$MEMBER" =~ ^[ABab]$$ && -n "$$RECOVERY_DIR" ]] || { echo 'Set PAIR, MEMBER=A or B, and a new RECOVERY_DIR' >&2; exit 2; }
	$(RUN) python3 stages/07-replace-member/prepare.py --membership "$$RECORDS_DIR/pairs/$$PAIR/current.json" --recovery-dir "$$RECOVERY_DIR" --pair "$$PAIR" --member "$${MEMBER,,}"

# Write exactly one blank USB, sharing the pair writer and upgrade lock.
write-replacement:
	@[[ "$$PAIR" =~ ^[1-9][0-9]*$$ && "$$MEMBER" =~ ^[ABab]$$ && -n "$$RECOVERY_DIR" && -n "$$DISK" ]] || { echo 'Set PAIR, MEMBER=A or B, RECOVERY_DIR and DISK; identify the blank USB with lsblk first' >&2; exit 2; }
	$(RUN) $(SUDO) python3 stages/07-replace-member/write.py --out "$$RECOVERY_DIR/pairs/$$PAIR" --pair "$$PAIR" --member "$${MEMBER,,}" --disk "$$DISK"

# Export small non-secret records without copying images into the durable directory.
save-records:
	$(RUN) python3 pipeline/records.py save --out "$$OUT" --pair "$$PAIR"

# Record the operator's confirmation after live recovery and file verification.
confirm-replacement:
	@test "$$CONFIRMED" = yes || { echo 'Set CONFIRMED=yes only after successful live recovery and read-check' >&2; exit 2; }
	$(RUN) python3 pipeline/records.py accept --pair "$$PAIR" --replacement "$$REPLACEMENT" --confirmed

# Stage generic files without unlocking or mounting the encrypted workspace.
stage-transport:
	@[[ '$(origin PAIR)' == 'command line' || '$(origin PAIR)' == environment ]] || { echo 'Set PAIR explicitly, e.g. PAIR=1' >&2; exit 2; }
	$(RUN) $(SUDO) python3 pipeline/stage-transport.py --pair "$$PAIR"

# Prepare repository bundles without mounting or writing a USB pair.
add-transport-git-bundle:
	@test -n "$$REPO" || { echo 'Set REPO to a Git repository URL' >&2; exit 2; }
	$(RUN) python3 pipeline/add-transport-git-bundle.py --repo "$$REPO"
