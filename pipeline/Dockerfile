# syntax=docker/dockerfile:1-labs
ARG DEBIAN_IMAGE=debian:trixie

# Install the build environment shared by the artifact stages.
FROM ${DEBIAN_IMAGE} AS toolchain
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    live-build debootstrap xorriso squashfs-tools dosfstools mtools \
    grub-efi-amd64-bin grub-common grub2-common \
    gdisk util-linux ca-certificates git python3 file cpio rsync kmod apt-utils initramfs-tools-core sudo passwd \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build

# Include only the stages that execute inside BuildKit and the shipped runtime.
FROM toolchain AS source
COPY stages/01-build-live/ /src/stages/01-build-live/
COPY stages/02-assemble-pair/ /src/stages/02-assemble-pair/
COPY runtime/ /src/runtime/

# Accept the full committed source history without local Git configuration.
FROM scratch AS repository-input

# Build the live ISO with the privileges needed by live-build mounts.
FROM source AS live
COPY --from=repository-input / /repository/
ARG LB_CONFIG_JSON=[]
RUN --security=insecure LEVEL1VAULT_REPOSITORY=/repository bash /src/stages/01-build-live/build.sh

# Assemble a fresh pair from the live ISO.
FROM source AS assembly
COPY --from=live /artifacts/live-image-amd64.hybrid.iso /input/live.iso
ARG ASSEMBLY_ID
RUN test -n "$ASSEMBLY_ID" && /src/stages/02-assemble-pair/assemble.sh /input/live.iso /out

# Export the ISO and prepared pair under their stage output directories.
FROM scratch AS artifacts
ARG PAIR_ID=1
COPY --from=live /artifacts/ /live/
COPY --from=assembly /out/ /pairs/${PAIR_ID}/

# Accept an existing ISO for an additional pair.
FROM scratch AS iso-input
FROM source AS pair-assembly
COPY --from=iso-input /live-image-amd64.hybrid.iso /input/live.iso
ARG ASSEMBLY_ID
ARG IMAGE_MIB=12288
ARG ESP_MIB=4096
RUN test -n "$ASSEMBLY_ID" && /src/stages/02-assemble-pair/assemble.sh /input/live.iso /out
FROM scratch AS pair-artifacts
COPY --from=pair-assembly /out/ /

# Verify each container stage using its own tests.
FROM source AS test-assembly
RUN mkdir /results && TEST_ISO_OUTPUT=/results/live-image-amd64.hybrid.iso bash /src/stages/02-assemble-pair/tests/test_assembly.sh && touch /results/assembly-passed
FROM source AS test-account
RUN bash /src/stages/01-build-live/tests/test_account.sh && mkdir /results && touch /results/account-passed
FROM scratch AS test-results
COPY --from=test-assembly /results/ /
COPY --from=test-account /results/ /

# Inspect the completed live system without initialized workspace images.
FROM scratch AS build-input
FROM source AS inspection
COPY --from=build-input / /inspect/
RUN bash /src/stages/01-build-live/inspect.sh /inspect --iso-only && mkdir /results && touch /results/inspection-passed
FROM scratch AS inspection-results
COPY --from=inspection /results/ /
