# syntax=docker/dockerfile:1-labs
ARG DEBIAN_IMAGE=debian:trixie
FROM ${DEBIAN_IMAGE} AS toolchain
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    live-build debootstrap xorriso squashfs-tools dosfstools mtools \
    isolinux syslinux-common grub-efi-amd64-bin grub-common grub2-common \
    gdisk util-linux ca-certificates python3 file cpio rsync kmod apt-utils initramfs-tools-core sudo passwd \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build

FROM toolchain AS source
COPY 01-configure-live.sh 02-assemble-images.sh /src/
COPY live/ /src/live/
COPY docker/build-in-container.sh /src/docker/build-in-container.sh

FROM source AS live
ARG LB_CONFIG_JSON=[]
# live-build mounts proc/sys/dev in its chroot. Only this RUN needs elevation.
RUN --security=insecure bash /src/docker/build-in-container.sh

FROM source AS assembly
COPY --from=live /artifacts/live-image-amd64.hybrid.iso /input/live.iso
# Consumed in RUN so each wrapper invocation invalidates only identity creation.
ARG ASSEMBLY_ID
RUN test -n "$ASSEMBLY_ID" && /src/02-assemble-images.sh /input/live.iso /out

FROM scratch AS artifacts
COPY --from=live /artifacts/ /
COPY --from=assembly /out/ /out/

# Overridden by a named context containing ONLY an existing ISO.
FROM scratch AS iso-input
FROM source AS pair-assembly
COPY --from=iso-input /live-image-amd64.hybrid.iso /input/live.iso
ARG ASSEMBLY_ID
ARG IMAGE_MIB=12288
ARG ESP_MIB=4096
RUN test -n "$ASSEMBLY_ID" && /src/02-assemble-images.sh /input/live.iso /out
FROM scratch AS pair-artifacts
COPY --from=pair-assembly /out/ /

FROM source AS test-source
COPY tests/ /src/tests/
FROM test-source AS test-assembly
RUN mkdir /results && TEST_ISO_OUTPUT=/results/live-image-amd64.hybrid.iso bash /src/tests/test_assembly.sh && touch /results/assembly-passed
FROM test-source AS test-account
RUN bash /src/tests/test_account.sh && mkdir /results && touch /results/account-passed
FROM scratch AS test-results
COPY --from=test-assembly /results/ /
COPY --from=test-account /results/ /

# The wrapper supplies a context limited to the ISO and prepared artifacts.
FROM scratch AS build-input
FROM test-source AS inspection
COPY --from=build-input / /inspect/
RUN bash /src/tests/test_live_build.sh /inspect --iso-only && mkdir /results && touch /results/inspection-passed
FROM scratch AS inspection-results
COPY --from=inspection /results/ /
