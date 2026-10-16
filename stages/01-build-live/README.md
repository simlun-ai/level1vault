# Stage 1: build the live system

Input: `packages.list`, `hooks/`, the shipped commands in `../../runtime/`, and optional live-build configuration arguments. Output: `BUILD_DIR/live/live-image-amd64.hybrid.iso` and `live-build.log`, plus build records at the run root.

`configure.sh` prepares Debian Trixie live-build configuration. `build.sh` builds the ISO inside the privileged BuildKit stage. `inspect.sh` checks the packaged kernel, ZFS module, account, services, and initramfs without booting a VM.

Boot support is UEFI-only (`grub-efi`); Syslinux/ISOLINUX, legacy memtest, and the Windows loader are disabled. The intermediate ISO retains the existing `live-image-amd64.hybrid.iso` artifact name and is consumed by stage 2, which creates independent GPT/FAT/EFI USB images. Image inspection requires an EFI El Torito entry and rejects BIOS entries.

`packages.list` describes the shipped userland. `build-packages.list` contains temporary ZFS compilation dependencies; `@KERNEL@` selects the one installed Debian kernel. The ZFS hook builds those modules, packages them independently with `package-zfs.py`, and removes the toolchain before SquashFS generation. The standalone package owns all ZFS modules in `extra/level1vault`, so purging DKMS cannot remove them. Its dependencies pin the kernel and ZFS userland. The image records both build-time and final package manifests; kernel or ZFS changes require a new image build.

Run `make build` to execute stages 1 and 2, then `make check-build` to inspect the prepared artifacts. The default run directory is `build/default`; use a new `BUILD_DIR` for each new build. Configure advanced live-build arguments through `sudo bash pipeline/buildkit.sh build NEW_BUILD_DIR ...`.

A failed build leaves diagnostic logs. Inspect those and retry with a fresh run directory; do not reuse a partially completed directory. BuildKit may reuse valid cached stages.

Tests live in `tests/`: `make test` runs configuration behavior checks and builds/extracts a real Debian module-package fixture, and `make test-container` verifies the real account/password/login setup in a disposable Debian build stage. `make check-build` requires a real completed ISO and verifies standalone module ownership plus the absence of build tools and headers. Module loadability is tested separately on the workstation.

See [build details](../../docs/BUILD.md), [BuildKit](../../docs/BUILDKIT.md), and the [documentation index](../../docs/README.md).
