#!/usr/bin/env bash
set -euo pipefail

# Read the requested action and build settings.
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
BUILDX_BUILDER=${BUILDX_BUILDER:-level1vault}
DEBIAN_IMAGE=${DEBIAN_IMAGE:-debian:trixie}
PAIR_ID=${PAIR:-1}
[[ $PAIR_ID =~ ^[1-9][0-9]*$ ]] || { echo 'PAIR must be a positive integer' >&2; exit 2; }
ACTION=${1:?Action required}
shift
[[ $(uname -m) == x86_64 ]] || { echo 'An amd64 Linux host is required' >&2; exit 1; }
# Never modify the user's selected builder. Always address our named instance.
[[ $BUILDX_BUILDER =~ ^level1vault([a-zA-Z0-9_.-]*)$ ]] || {
  echo 'Use a dedicated builder name beginning with level1vault' >&2; exit 1;
}

# Check the local Docker and Buildx prerequisites.
docker buildx version >/dev/null
ENDPOINT=${DOCKER_HOST:-$(docker context inspect --format '{{(index .Endpoints "docker").Host}}')}
if [[ -n ${DOCKER_CONTEXT:-} ]]; then
  ENDPOINT=$(docker context inspect "$DOCKER_CONTEXT" --format '{{(index .Endpoints "docker").Host}}')
fi
[[ $ENDPOINT == unix://* ]] || { echo 'Use local rootful Docker Engine' >&2; exit 1; }
python3 - "$(docker info --format '{{json .}}')" <<'PY'
import json, sys
info = json.loads(sys.argv[1])
if info.get('OSType') != 'linux' or info.get('Architecture') not in ('x86_64', 'amd64'):
    sys.exit('Docker must run locally on amd64 Linux')
if any('rootless' in x for x in info.get('SecurityOptions', [])):
    sys.exit('Use rootful Docker Engine')
PY

# Prepare the dedicated project builder.
if ! docker buildx inspect "$BUILDX_BUILDER" >/dev/null 2>&1; then
  docker buildx create --name "$BUILDX_BUILDER" --driver docker-container \
    --buildkitd-flags '--allow-insecure-entitlement security.insecure' >/dev/null
fi
DRIVER=$(LC_ALL=C docker buildx inspect "$BUILDX_BUILDER" | awk '$1 == "Driver:" {print $2}')
[[ $DRIVER == docker-container ]] || { echo 'Dedicated builder must use docker-container driver' >&2; exit 1; }
docker buildx inspect "$BUILDX_BUILDER" --bootstrap >/dev/null

# Define the shared artifact build settings.
base=(docker buildx build --builder "$BUILDX_BUILDER" --platform linux/amd64 \
  --progress plain --build-arg "DEBIAN_IMAGE=$DEBIAN_IMAGE" -f "$SOURCE_DIR/pipeline/Dockerfile")

# Remove temporary task files when the command finishes.
TASK_TMP=
cleanup() { if [[ -n $TASK_TMP ]]; then rm -rf -- "$TASK_TMP"; fi; }
trap cleanup EXIT

# Reject existing or unsupported output destinations.
new_destination() {
  DEST=$(realpath -m -- "$1")
  [[ $DEST != *,* && ! -e $DEST ]] || { echo "Use a new output directory without commas: $DEST" >&2; exit 1; }
}

# Run the selected build lifecycle action.
case $ACTION in
  builder) ;;

  # Build and export a live ISO and a fresh image pair.
  build|build-upgrade)
    new_destination "${1:?Build directory required}"
    shift
    LB_CONFIG_JSON=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@")
    fresh=()
    [[ ${FRESH:-0} == 0 || ${FRESH:-0} == 1 ]] || { echo 'FRESH must be 0 or 1' >&2; exit 1; }
    if [[ ${FRESH:-0} == 1 ]]; then fresh=(--no-cache --pull); fi
    TASK_TMP=$(mktemp -d)
    python3 "$SOURCE_DIR/stages/01-build-live/repository.py" bundle "$SOURCE_DIR" "$TASK_TMP/repository"
    mkdir -p -- "$DEST"
    docker buildx inspect "$BUILDX_BUILDER" > "$DEST/builder-info.txt"
    target=artifacts
    if [[ $ACTION == build-upgrade ]]; then target=upgrade-artifacts; fi
    # No initialized images enter the context, and identities never come from cache.
    "${base[@]}" "${fresh[@]}" --target "$target" --allow security.insecure \
      --build-context "repository-input=$TASK_TMP/repository" \
      --build-arg "LB_CONFIG_JSON=$LB_CONFIG_JSON" --build-arg "PAIR_ID=$PAIR_ID" \
      --build-arg "ASSEMBLY_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')" \
      --metadata-file "$DEST/build-metadata.json" \
      --output "type=local,dest=$DEST,platform-split=false" "$SOURCE_DIR" 2>&1 | tee "$DEST/build.log"
    ;;

  # Export another image pair from an existing ISO.
  assemble)
    BUILD_DIR=$(realpath -e -- "${1:?Build directory required}")
    new_destination "${2:?Output directory required}"
    [[ $DEST == "$BUILD_DIR/"* ]] || { echo 'OUT must be beneath BUILD_DIR' >&2; exit 1; }
    TASK_TMP=$(mktemp -d)
    # Send only the ISO, never the directory containing initialized ZFS images.
    cp --reflink=auto --sparse=always "$BUILD_DIR/live/live-image-amd64.hybrid.iso" "$TASK_TMP/"
    "${base[@]}" --target pair-artifacts --build-context "iso-input=$TASK_TMP" \
      --build-arg "ASSEMBLY_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')" \
      --output "type=local,dest=$DEST,platform-split=false" "$SOURCE_DIR"
    ;;

  # Verify container behavior and distinct exported image identities.
  test)
    TASK_TMP=$(mktemp -d)
    "${base[@]}" --target test-results --no-cache-filter test-assembly,test-account \
      --output "type=local,dest=$TASK_TMP,platform-split=false" "$SOURCE_DIR"
    test -f "$TASK_TMP/assembly-passed" && test -f "$TASK_TMP/account-passed"
    # Exercise the actual scratch/local export twice with identical ISO input.
    mkdir "$TASK_TMP/iso"
    mv "$TASK_TMP/live-image-amd64.hybrid.iso" "$TASK_TMP/iso/"
    for pair in 1 2; do
      "${base[@]}" --target pair-artifacts --build-context "iso-input=$TASK_TMP/iso" \
        --build-arg IMAGE_MIB=512 --build-arg ESP_MIB=64 \
        --build-arg "ASSEMBLY_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')" \
        --output "type=local,dest=$TASK_TMP/pair-$pair,platform-split=false" "$SOURCE_DIR"
      (cd "$TASK_TMP/pair-$pair" && sha256sum -c PREPARED-SHA256SUMS)
    done
    python3 "$SOURCE_DIR/stages/02-assemble-pair/tests/check_exports.py" "$TASK_TMP"
    ;;

  # Inspect the exported live system and prepared images.
  check)
    BUILD_DIR=$(realpath -e -- "${1:?Build directory required}")
    OUT_DIR=$(realpath -e -- "${2:-$BUILD_DIR/pairs/$PAIR_ID}")
    [[ -f $OUT_DIR/IMAGES-PREPARED && ! -e $OUT_DIR/READY && ! -e $OUT_DIR/INITIALIZING ]] || {
      echo 'Inspect only uninitialized prepared artifacts' >&2; exit 1;
    }
    (cd "$OUT_DIR" && sha256sum -c PREPARED-SHA256SUMS)
    TASK_TMP=$(mktemp -d)
    mkdir "$TASK_TMP/input"
    cp --reflink=auto --sparse=always "$BUILD_DIR/live/live-image-amd64.hybrid.iso" "$TASK_TMP/input/"
    "${base[@]}" --target inspection-results --build-context "build-input=$TASK_TMP/input" \
      --no-cache-filter inspection --output "type=local,dest=$TASK_TMP/result,platform-split=false" "$SOURCE_DIR"
    test -f "$TASK_TMP/result/inspection-passed"
    ;;

  # Prune unused cache belonging to the project builder.
  cache-clean)
    # Prune only unused cache on this dedicated builder; never host output files.
    docker buildx prune --builder "$BUILDX_BUILDER" --force
    ;;
  *) echo 'Usage: buildkit.sh builder|build DIR [LB_ARGS...]|build-upgrade DIR|assemble DIR OUT|test|check DIR|cache-clean' >&2; exit 2 ;;
esac
