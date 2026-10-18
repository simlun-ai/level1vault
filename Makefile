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
export SUDO BUILD_DIR OUT PAIR ACTIVE BUILDX_BUILDER DEBIAN_IMAGE FRESH DISK_A DISK_B N UPGRADE_DIR

RUN = python3 pipeline/lifecycle.py shared

# Expose the supported project lifecycle commands.
.PHONY: help prepare-pairs build builder assemble initialize lint test-unit test test-container test-zfs test-qemu write-usb upgrade-os check-build clean cache-clean
# Show the common build, test, and cleanup commands.
help:
	@printf '%s\n' \
	  'make prepare-pairs N=3           Prepare one active pair and N-1 populated replicas' \
	  'make build                       Build live ISO and first uninitialized image pair' \
	  'make initialize PAIR=1 ACTIVE=1   Create the first encrypted, writable workspace' \
	  'make assemble PAIR=2              Prepare another pair from the existing ISO' \
	  'make initialize PAIR=2            Initialize an empty replica' \
	  'make write-usb DISK_A=/dev/sda DISK_B=/dev/sdb  Write the selected USB pair (destructive)' \
	  'make upgrade-os PAIR=2 DISK_A=/dev/sda DISK_B=/dev/sdb  Replace the existing OS only' \
	  'make test                        Run local lint and unit/behavior tests' \
	  'make test-container              Test real assembly and login in Trixie' \
	  'make test-qemu                    Test pair 1 in QEMU; choose writable results automatically' \
	  'make test-zfs                     Real ZFS tests; disposable VM only (see docs)' \
	  'make check-build                 Inspect an already-built live system' \
	  'make clean                       Remove temporary work and checkout BuildKit state; preserve artifacts' \
	  'make cache-clean                 Prune unused cache on the dedicated builder' \
	  'Options: BUILD_DIR=build-next, OUT=..., SUDO= (when already root)'

# Prepare a complete initial set with all authentication and secret prompts upfront.
prepare-pairs:
	@[[ "$$N" =~ ^[1-9][0-9]*$$ ]] || { echo 'Set N to a positive pair count, e.g. N=3' >&2; exit 2; }
	$(RUN) $(SUDO) env LEVEL1VAULT_LOCKED=1 BUILDX_BUILDER="$$BUILDX_BUILDER" DEBIAN_IMAGE="$$DEBIAN_IMAGE" FRESH="$$FRESH" python3 pipeline/prepare-pairs.py --build-dir "$$BUILD_DIR" --count "$$N"

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

# Write an initialized pair to two explicitly identified whole USB disks.
write-usb:
	@[[ -n "$$DISK_A" && -n "$$DISK_B" ]] || { echo 'Set both DISK_A and DISK_B; identify disks with lsblk first' >&2; exit 2; }
	$(RUN) $(SUDO) python3 stages/05-write-usb/write.py --out "$$OUT" --pair "$$PAIR" --disk-a "$$DISK_A" --disk-b "$$DISK_B"

# Rebuild the OS and upgrade one explicitly identified existing pair in place.
upgrade-os:
	@[[ '$(origin PAIR)' == 'command line' || '$(origin PAIR)' == environment ]] || { echo 'Set PAIR explicitly for an OS upgrade' >&2; exit 2; }
	@[[ "$$PAIR" =~ ^[1-9][0-9]*$$ && -n "$$DISK_A" && -n "$$DISK_B" ]] || { echo 'Set PAIR, DISK_A and DISK_B; identify disks with lsblk first' >&2; exit 2; }
	$(RUN) $(SUDO) env LEVEL1VAULT_LOCKED=1 BUILDX_BUILDER="$$BUILDX_BUILDER" DEBIAN_IMAGE="$$DEBIAN_IMAGE" FRESH="$$FRESH" python3 pipeline/upgrade-os.py --pair "$$PAIR" --disk-a "$$DISK_A" --disk-b "$$DISK_B" --release "$$UPGRADE_DIR"

# Check shell scripts for syntax and lint errors.
lint:
	bash pipeline/lint.sh

# These tests mock host mutations; no sudo, Docker or loaded ZFS module needed.
test-unit:
	python3 pipeline/test.py

# Run the workstation-safe checks.
test: lint test-unit

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

# Remove temporary local and Docker-managed state while preserving artifacts.
clean:
	python3 pipeline/lifecycle.py clean "$$BUILD_DIR"

# Prune unused project build cache.
cache-clean:
	$(RUN) env PAIR="$$PAIR" BUILDX_BUILDER="$$BUILDX_BUILDER" bash pipeline/buildkit.sh cache-clean
