#!/usr/bin/env bash
set -euo pipefail
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
BUILDX_BUILDER=${BUILDX_BUILDER:-level1vault}
DEBIAN_IMAGE=${DEBIAN_IMAGE:-debian:trixie}
ACTION=${1:?Action required}
shift
[[ $(uname -m) == x86_64 ]] || { echo 'An amd64 Linux host is required' >&2; exit 1; }
# Never modify the user's selected builder. Always address our named instance.
[[ $BUILDX_BUILDER =~ ^level1vault([a-zA-Z0-9_.-]*)$ ]] || {
  echo 'Use a dedicated builder name beginning with level1vault' >&2; exit 1;
}
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
if ! docker buildx inspect "$BUILDX_BUILDER" >/dev/null 2>&1; then
  docker buildx create --name "$BUILDX_BUILDER" --driver docker-container \
    --buildkitd-flags '--allow-insecure-entitlement security.insecure' >/dev/null
fi
DRIVER=$(docker buildx inspect "$BUILDX_BUILDER" --format '{{.Driver}}')
[[ $DRIVER == docker-container ]] || { echo 'Dedicated builder must use docker-container driver' >&2; exit 1; }
docker buildx inspect "$BUILDX_BUILDER" --bootstrap >/dev/null
base=(docker buildx build --builder "$BUILDX_BUILDER" --platform linux/amd64 \
  --progress plain --build-arg "DEBIAN_IMAGE=$DEBIAN_IMAGE" -f "$SOURCE_DIR/docker/Dockerfile")
TASK_TMP=
cleanup() { if [[ -n $TASK_TMP ]]; then rm -rf -- "$TASK_TMP"; fi; }
trap cleanup EXIT
new_destination() {
  DEST=$(realpath -m -- "$1")
  [[ $DEST != *,* && ! -e $DEST ]] || { echo "Use a new output directory without commas: $DEST" >&2; exit 1; }
}
case $ACTION in
  builder) ;;
  build)
    new_destination "${1:?Build directory required}"
    shift
    LB_CONFIG_JSON=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@")
    fresh=()
    [[ ${FRESH:-0} == 0 || ${FRESH:-0} == 1 ]] || { echo 'FRESH must be 0 or 1' >&2; exit 1; }
    if [[ ${FRESH:-0} == 1 ]]; then fresh=(--no-cache --pull); fi
    mkdir -p -- "$DEST"
    docker buildx inspect "$BUILDX_BUILDER" > "$DEST/builder-info.txt"
    # No initialized images enter the context, and identities never come from cache.
    "${base[@]}" "${fresh[@]}" --target artifacts --allow security.insecure \
      --build-arg "LB_CONFIG_JSON=$LB_CONFIG_JSON" \
      --build-arg "ASSEMBLY_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')" \
      --metadata-file "$DEST/build-metadata.json" \
      --output "type=local,dest=$DEST,platform-split=false" "$SOURCE_DIR" 2>&1 | tee "$DEST/build.log"
    ;;
  assemble)
    BUILD_DIR=$(realpath -e -- "${1:?Build directory required}")
    new_destination "${2:?Output directory required}"
    [[ $DEST == "$BUILD_DIR/"* ]] || { echo 'OUT must be beneath BUILD_DIR' >&2; exit 1; }
    TASK_TMP=$(mktemp -d)
    # Send only the ISO, never the directory containing initialized ZFS images.
    cp --reflink=auto --sparse=always "$BUILD_DIR/live-image-amd64.hybrid.iso" "$TASK_TMP/"
    "${base[@]}" --target pair-artifacts --build-context "iso-input=$TASK_TMP" \
      --build-arg "ASSEMBLY_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')" \
      --output "type=local,dest=$DEST,platform-split=false" "$SOURCE_DIR"
    ;;
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
    python3 - "$TASK_TMP" <<'PYIDENTITY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
seen = set()
for pair in (1, 2):
    out = root/f'pair-{pair}'
    assert (out/'IMAGES-PREPARED').exists() and not (out/'READY').exists()
    manifest = json.loads((out/'images.json').read_text())
    for disk in manifest['images'].values():
        for prop in ('esp_uuid', 'zfs_partuuid'):
            assert disk[prop] not in seen, 'BuildKit reused a disk identity'
            seen.add(disk[prop])
PYIDENTITY
    ;;
  check)
    BUILD_DIR=$(realpath -e -- "${1:?Build directory required}")
    [[ -f $BUILD_DIR/out/IMAGES-PREPARED && ! -e $BUILD_DIR/out/READY && ! -e $BUILD_DIR/out/INITIALIZING ]] || {
      echo 'Inspect only uninitialized prepared artifacts' >&2; exit 1;
    }
    (cd "$BUILD_DIR/out" && sha256sum -c PREPARED-SHA256SUMS)
    TASK_TMP=$(mktemp -d)
    mkdir "$TASK_TMP/input"
    cp --reflink=auto --sparse=always "$BUILD_DIR/live-image-amd64.hybrid.iso" "$TASK_TMP/input/"
    "${base[@]}" --target inspection-results --build-context "build-input=$TASK_TMP/input" \
      --no-cache-filter inspection --output "type=local,dest=$TASK_TMP/result,platform-split=false" "$SOURCE_DIR"
    test -f "$TASK_TMP/result/inspection-passed"
    ;;
  cache-clean)
    # Prune only unused cache on this dedicated builder; never host output files.
    docker buildx prune --builder "$BUILDX_BUILDER" --force
    ;;
  *) echo 'Usage: buildkit.sh builder|build DIR [LB_ARGS...]|assemble DIR OUT|test|check DIR|cache-clean' >&2; exit 2 ;;
esac
