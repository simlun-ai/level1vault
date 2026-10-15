.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
# Serialize lifecycle targets even when invoked with make -j.
.NOTPARALLEL:

BUILD_DIR ?= build
OUT ?= $(BUILD_DIR)/out
PAIR ?=
ACTIVE ?= 0
BUILDER_IMAGE ?= level1vault-builder:trixie
DEBIAN_IMAGE ?= debian:trixie
SUDO ?= sudo
export BUILD_DIR OUT PAIR ACTIVE BUILDER_IMAGE DEBIAN_IMAGE

.PHONY: help build builder assemble initialize verify lint test-unit test test-container test-zfs check-build clean
help:
	@printf '%s\n' \
	  'make build                       Build live ISO and first uninitialized image pair' \
	  'make initialize PAIR=1 ACTIVE=1   Create the first encrypted, writable workspace' \
	  'make assemble OUT=build/pair-2    Prepare another pair from the existing ISO' \
	  'make initialize PAIR=2 OUT=build/pair-2  Initialize an empty replica' \
	  'make verify [OUT=...]             Check READY and final image checksums' \
	  'make test                        Run local lint and unit/behavior tests' \
	  'make test-container              Test real assembly and login in Trixie' \
	  'make test-zfs                     Real ZFS tests; disposable VM only (see docs)' \
	  'make check-build                 Inspect an already-built live system' \
	  'make clean                       Remove build work; preserve images/ISO/records' \
	  'Options: BUILD_DIR=build-next, OUT=..., SUDO= (when already root)'

build:
	$(SUDO) env BUILDER_IMAGE="$$BUILDER_IMAGE" DEBIAN_IMAGE="$$DEBIAN_IMAGE" ./build-container.sh "$$BUILD_DIR"

builder:
	$(SUDO) docker build --platform linux/amd64 --build-arg "DEBIAN_IMAGE=$$DEBIAN_IMAGE" -t "$$BUILDER_IMAGE" -f docker/Dockerfile docker

assemble:
	$(SUDO) env BUILDER_IMAGE="$$BUILDER_IMAGE" bash scripts/container-task.sh assemble "$$BUILD_DIR" "$$OUT"

initialize:
	@[[ "$$PAIR" =~ ^[1-9][0-9]*$$ ]] || { echo 'Set PAIR to a positive integer, e.g. PAIR=1' >&2; exit 1; }
	@[[ "$$ACTIVE" == 0 || "$$ACTIVE" == 1 ]] || { echo 'ACTIVE must be 0 or 1' >&2; exit 1; }
	$(SUDO) ./03-initialize-workspace.sh "$$OUT" "$$PAIR" $$( [[ "$$ACTIVE" == 1 ]] && printf '%s' --active || : )

verify:
	@test -f "$$OUT/READY" || { echo "Not ready: $$OUT/READY is missing" >&2; exit 1; }
	cd "$$OUT" && sha256sum -c SHA256SUMS

lint:
	shellcheck ./*.sh docker/*.sh scripts/*.sh tests/*.sh live/workspace-mount live/workspace-umount live/workspace-activate live/*.hook.chroot
	@for f in ./*.sh docker/*.sh scripts/*.sh tests/*.sh; do bash -n "$$f"; done
	@for f in live/workspace-mount live/workspace-umount live/workspace-activate live/*.hook.chroot; do sh -n "$$f"; done

# These tests mock host mutations; no sudo, Docker or loaded ZFS module needed.
test-unit:
	python3 -m unittest discover -s tests -v

test: lint test-unit

test-container: builder
	$(SUDO) env BUILDER_IMAGE="$$BUILDER_IMAGE" bash scripts/container-task.sh test

# Deliberately excluded from `make test`: do not opt a workstation into this.
test-zfs:
	@test "$${LEVEL1VAULT_DISPOSABLE_TEST_VM:-}" = 1 || { echo 'Use a disposable VM and set LEVEL1VAULT_DISPOSABLE_TEST_VM=1' >&2; exit 1; }
	$(SUDO) env LEVEL1VAULT_DISPOSABLE_TEST_VM=1 python3 tests/integration_zfs.py

check-build:
	$(SUDO) env BUILDER_IMAGE="$$BUILDER_IMAGE" bash scripts/container-task.sh check "$$BUILD_DIR"

clean:
	$(SUDO) python3 scripts/clean.py "$$BUILD_DIR"
