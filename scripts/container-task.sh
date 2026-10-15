#!/usr/bin/env bash
set -euo pipefail
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
BUILDER_IMAGE=${BUILDER_IMAGE:-level1vault-builder:trixie}
[[ $SOURCE_DIR != *,* ]] || { echo 'Docker bind paths cannot contain commas' >&2; exit 1; }
mounts=(--mount "type=bind,source=$SOURCE_DIR,target=/src,readonly")
case ${1:-} in
  test)
    docker run --rm "${mounts[@]}" "$BUILDER_IMAGE" bash /src/tests/test_assembly.sh
    # Separate containers keep the test account changes isolated.
    docker run --rm "${mounts[@]}" "$BUILDER_IMAGE" bash /src/tests/test_account.sh
    ;;
  assemble|check)
    BUILD_DIR=$(realpath -e -- "${2:?Build directory required}")
    [[ $BUILD_DIR != *,* ]] || { echo 'Docker bind paths cannot contain commas' >&2; exit 1; }
    if [[ $1 == assemble ]]; then
      OUT=$(realpath -m -- "${3:?Output directory required}")
      [[ $OUT == "$BUILD_DIR/"* && $OUT != *,* && ! -e $OUT ]] || {
        echo 'OUT must be a new directory beneath BUILD_DIR' >&2; exit 1;
      }
      docker run --rm "${mounts[@]}" --mount "type=bind,source=$BUILD_DIR,target=/build" \
        "$BUILDER_IMAGE" bash /src/02-assemble-images.sh \
        /build/live-image-amd64.hybrid.iso "/build/${OUT#"$BUILD_DIR/"}"
    else
      docker run --rm "${mounts[@]}" --mount "type=bind,source=$BUILD_DIR,target=/build,readonly" \
        "$BUILDER_IMAGE" bash /src/tests/test_live_build.sh /build
    fi
    ;;
  *) echo 'Usage: container-task.sh test | assemble BUILD_DIR OUT | check BUILD_DIR' >&2; exit 1 ;;
esac
