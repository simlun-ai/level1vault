# syntax=docker/dockerfile:1-labs

# Select the Debian build environment.
ARG DEBIAN_IMAGE=debian:trixie

# Install the live image and assembly toolchain.
FROM ${DEBIAN_IMAGE} AS toolchain
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    live-build debootstrap xorriso squashfs-tools dosfstools mtools \
    isolinux syslinux-common grub-efi-amd64-bin grub-common grub2-common \
    gdisk util-linux ca-certificates python3 file cpio rsync kmod apt-utils initramfs-tools-core sudo passwd \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build


# Include the live system source files.
FROM toolchain AS source
COPY 01-configure-live.sh 02-assemble-images.sh /src/
COPY live/ /src/live/
COPY docker/build-in-container.sh /src/docker/build-in-container.sh


# Build the live ISO.
FROM source AS live
ARG LB_CONFIG_JSON=[]
# live-build mounts proc/sys/dev in its chroot. Only this RUN needs elevation.
RUN --security=insecure bash /src/docker/build-in-container.sh


# Assemble the first independently bootable image pair.
FROM source AS assembly
COPY --from=live /artifacts/live-image-amd64.hybrid.iso /input/live.iso
# Consumed in RUN so each wrapper invocation invalidates only identity creation.
ARG ASSEMBLY_ID
RUN test -n "$ASSEMBLY_ID" && /src/02-assemble-images.sh /input/live.iso /out


# Export the live ISO, image pair, and build records.
FROM scratch AS artifacts
COPY --from=live /artifacts/ /
COPY --from=assembly /out/ /out/

# Accept an existing ISO for additional pairs.
FROM scratch AS iso-input
FROM source AS pair-assembly
COPY --from=iso-input /live-image-amd64.hybrid.iso /input/live.iso
ARG ASSEMBLY_ID
ARG IMAGE_MIB=12288
ARG ESP_MIB=4096
RUN test -n "$ASSEMBLY_ID" && /src/02-assemble-images.sh /input/live.iso /out

# Export another prepared image pair.
FROM scratch AS pair-artifacts
COPY --from=pair-assembly /out/ /


# Include the container test suite.
FROM source AS test-source
COPY tests/ /src/tests/

# Verify real image assembly.
FROM test-source AS test-assembly
RUN mkdir /results && TEST_ISO_OUTPUT=/results/live-image-amd64.hybrid.iso bash /src/tests/test_assembly.sh && touch /results/assembly-passed

# Verify the live account and login message.
FROM test-source AS test-account
RUN bash /src/tests/test_account.sh && mkdir /results && touch /results/account-passed

# Export container test results.
FROM scratch AS test-results
COPY --from=test-assembly /results/ /
COPY --from=test-account /results/ /

# Accept the exported ISO for inspection.
FROM scratch AS build-input

# Inspect the completed live system.
FROM test-source AS inspection
COPY --from=build-input / /inspect/
RUN bash /src/tests/test_live_build.sh /inspect --iso-only && mkdir /results && touch /results/inspection-passed

# Export live system inspection results.
FROM scratch AS inspection-results
COPY --from=inspection /results/ /
