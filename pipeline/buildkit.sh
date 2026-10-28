#!/usr/bin/env bash
set -euo pipefail

# Read the requested action and build settings.
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# Enter the invoking user's Docker configuration before creating any files.
if [[ $EUID == 0 && ${SUDO_UID:-0} != 0 ]]; then
  exec python3 "$SOURCE_DIR/pipeline/lifecycle.py" user bash "$SOURCE_DIR/pipeline/buildkit.sh" "$@"
fi
if [[ ${LEVEL1VAULT_LOCKED:-0} != 1 ]]; then
  if [[ ${1:-} == clean ]]; then
    echo 'Use make clean to coordinate local and Docker cleanup' >&2; exit 1
  fi
  exec python3 "$SOURCE_DIR/pipeline/lifecycle.py" shared bash "$SOURCE_DIR/pipeline/buildkit.sh" "$@"
fi
BUILDX_BUILDER=${BUILDX_BUILDER:-$(python3 "$SOURCE_DIR/pipeline/lifecycle.py" builder-name)}

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

# Resolve checkout-local ownership records without relying on the caller's cwd.
BUILDER_RECORD=$(cd "$SOURCE_DIR" && realpath -m "$(git rev-parse --git-path "level1vault-builders/$BUILDX_BUILDER")")

# Remove only builders created by this checkout; never create one during cleanup.
if [[ $ACTION == clean ]]; then
  shopt -s nullglob
  records=("$(dirname -- "$BUILDER_RECORD")"/*)
  if [[ ${#records[@]} == 0 ]]; then
    echo 'No recorded checkout BuildKit state to clean'
  fi
  for record in "${records[@]}"; do
    [[ -f $record && ! -L $record && $(cat "$record") == "$SOURCE_DIR" ]] || {
      echo 'Builder ownership differs; refusing cleanup' >&2; exit 1;
    }
    builder=${record##*/}
    [[ $builder =~ ^level1vault([a-zA-Z0-9_.-]*)$ ]] || exit 1
    docker buildx rm "$builder"
    rm -- "$record"
  done
  exit 0
fi

# Prepare the dedicated project builder.
if ! docker buildx inspect "$BUILDX_BUILDER" >/dev/null 2>&1; then
  docker buildx create --name "$BUILDX_BUILDER" --driver docker-container \
    --buildkitd-flags '--allow-insecure-entitlement security.insecure' >/dev/null
  mkdir -p -- "$(dirname -- "$BUILDER_RECORD")"
  printf '%s\n' "$SOURCE_DIR" > "$BUILDER_RECORD"
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
    target='artifacts'
    if [[ $ACTION == build-upgrade ]]; then target='upgrade-artifacts'; fi
    # No initialized images enter the context, and identities never come from cache.
    "${base[@]}" "${fresh[@]}" --target "$target" --allow security.insecure \
      --build-context "repository-input=$TASK_TMP/repository" \
      --build-arg "LB_CONFIG_JSON=$LB_CONFIG_JSON" --build-arg "PAIR_ID=$PAIR_ID" \
      --build-arg "ASSEMBLY_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')" \
      --metadata-file "$DEST/build-metadata.json" \
      --output "type=local,dest=$DEST,platform-split=false" "$SOURCE_DIR" 2>&1 | tee "$DEST/build.log"
    if [[ $ACTION == build ]]; then
      bash "$SOURCE_DIR/pipeline/pair-archive.sh" unpack "$DEST/pairs/$PAIR_ID.tar" "$DEST/pairs/$PAIR_ID"
      rm -- "$DEST/pairs/$PAIR_ID.tar"
    fi
    ;;

  # Export another image pair from an existing ISO.
  assemble|release)
    BUILD_DIR=$(realpath -e -- "${1:?Build directory required}")
    new_destination "${2:?Output directory required}"
    [[ $DEST == "$BUILD_DIR/"* ]] || { echo 'OUT must be beneath BUILD_DIR' >&2; exit 1; }
    TASK_TMP=$(mktemp -d)
    # Send only the ISO, never the directory containing initialized ZFS images.
    cp --reflink=auto --sparse=always "$BUILD_DIR/live/live-image-amd64.hybrid.iso" "$TASK_TMP/"
    target='pair-artifacts'
    export_dest=$TASK_TMP/export
    if [[ $ACTION == release ]]; then target='upgrade-from-iso-artifacts'; export_dest=$DEST; fi
    "${base[@]}" --target "$target" --build-context "iso-input=$TASK_TMP" \
      --build-arg "ASSEMBLY_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')" \
      --output "type=local,dest=$export_dest,platform-split=false" "$SOURCE_DIR"
    if [[ $ACTION == assemble ]]; then
      bash "$SOURCE_DIR/pipeline/pair-archive.sh" unpack "$TASK_TMP/export/pair.tar" "$DEST"
    fi
    ;;

  # Run the Bake graph and verify actual local exports as the caller.
  ruff|typecheck)
    (cd "$SOURCE_DIR" && docker buildx bake --builder "$BUILDX_BUILDER" --file docker-bake.hcl --progress=plain "$ACTION")
    ;;

  ci|test)
    group=ci
    if [[ $ACTION == test ]]; then group=container; fi
    export BUILDX_BUILDER DEBIAN_IMAGE
    bash "$SOURCE_DIR/pipeline/ci.sh" "$group"
    ;;

  # Inspect the exported live system and prepared images.
  check)
    BUILD_DIR=$(realpath -e -- "${1:?Build directory required}")
    OUT_DIR=$(realpath -e -- "${2:-$BUILD_DIR/pairs/$PAIR_ID}")
    [[ -f $OUT_DIR/IMAGES-PREPARED && ! -e $OUT_DIR/READY && ! -e $OUT_DIR/INITIALIZING ]] || {
      echo 'Inspect only uninitialized prepared artifacts' >&2; exit 1;
    }
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
  *) echo 'Usage: buildkit.sh builder|build DIR [LB_ARGS...]|build-upgrade DIR|assemble DIR OUT|release DIR OUT|ci|test|check DIR|cache-clean' >&2; exit 2 ;;
esac
