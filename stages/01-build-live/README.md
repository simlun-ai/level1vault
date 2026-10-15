# Stage 1: build the live system

Input: `packages.list`, `hooks/`, the shipped commands in `../../runtime/`, and optional live-build configuration arguments. Output: `BUILD_DIR/live/live-image-amd64.hybrid.iso` and `live-build.log`, plus build records at the run root.

`configure.sh` prepares Debian Trixie live-build configuration. `build.sh` builds the ISO inside the privileged BuildKit stage. `inspect.sh` checks the packaged kernel, ZFS module, account, services, and initramfs without booting a VM.

Run `make build` to execute stages 1 and 2, then `make check-build` to inspect the prepared artifacts. The default run directory is `build/default`; use a new `BUILD_DIR` for each new build. Configure advanced live-build arguments through `sudo bash pipeline/buildkit.sh build NEW_BUILD_DIR ...`.

A failed build leaves diagnostic logs. Inspect those and retry with a fresh run directory; do not reuse a partially completed directory. BuildKit may reuse valid cached stages.

Tests live in `tests/`: `make test` runs configuration behavior checks, and `make test-container` verifies the real account/password/login setup in a disposable Debian build stage. `make check-build` requires a real completed ISO.

See [build details](../../docs/BUILD.md), [BuildKit](../../docs/BUILDKIT.md), and the [documentation index](../../docs/README.md).
