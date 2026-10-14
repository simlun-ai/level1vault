#!/usr/bin/env bash
set -euo pipefail
# Local, rootful Docker Engine on an amd64 Linux workstation.
# Usage: ./build-container.sh [NEW_BUILD_DIRECTORY] [extra lb config arguments ...]
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BUILD_DIR=$(realpath -m -- "${1:-$SOURCE_DIR/build}")
if [[ $# -gt 0 ]]; then shift; fi
[[ $(uname -m) == x86_64 ]] || { echo 'An amd64 Linux host is required' >&2; exit 1; }
[[ ! -e $BUILD_DIR ]] || { echo "Refusing existing build directory: $BUILD_DIR" >&2; exit 1; }
# Docker --mount uses commas as separators.
[[ $SOURCE_DIR != *,* && $BUILD_DIR != *,* ]] || { echo 'Build/source paths cannot contain commas' >&2; exit 1; }
command -v docker >/dev/null || { echo 'Install Docker Engine on the host first' >&2; exit 1; }
# Bind mounts refer to daemon-local paths, so a remote daemon cannot use this checkout.
DOCKER_ENDPOINT=${DOCKER_HOST:-$(docker context inspect --format '{{(index .Endpoints "docker").Host}}')}
if [[ -n ${DOCKER_CONTEXT:-} ]]; then
  DOCKER_ENDPOINT=$(docker context inspect "$DOCKER_CONTEXT" --format '{{(index .Endpoints "docker").Host}}')
fi
[[ $DOCKER_ENDPOINT == unix://* ]] || { echo 'Use a local Docker Engine through a Unix socket' >&2; exit 1; }
DOCKER_INFO=$(docker info --format '{{json .}}')
python3 - "$DOCKER_INFO" <<'PYINFO'
import json, sys
info = json.loads(sys.argv[1])
if info.get('OSType') != 'linux' or info.get('Architecture') not in ('x86_64', 'amd64'):
    sys.exit('Docker must run locally on amd64 Linux')
if any('rootless' in option for option in info.get('SecurityOptions', [])):
    sys.exit('Use rootful Docker Engine for live-build mounts')
PYINFO
BUILDER_IMAGE=${BUILDER_IMAGE:-level1vault-builder:trixie}
docker build --platform linux/amd64 \
  --build-arg "DEBIAN_IMAGE=${DEBIAN_IMAGE:-debian:trixie}" \
  -t "$BUILDER_IMAGE" -f "$SOURCE_DIR/docker/Dockerfile" "$SOURCE_DIR/docker"
mkdir -p -- "$BUILD_DIR"
docker image inspect "$BUILDER_IMAGE" > "$BUILD_DIR/builder-image.json"
# live-build needs chroot/mount capabilities. Do not share host /lib/modules,
# /run/udev or the Docker socket; pool creation is a separate host operation.
# --privileged is host-root-equivalent: run only trusted source/configuration.
docker run --rm --privileged --platform linux/amd64 \
  --mount "type=bind,source=$SOURCE_DIR,target=/src,readonly" \
  --mount "type=bind,source=$BUILD_DIR,target=/build,bind-propagation=rprivate" \
  "$BUILDER_IMAGE" bash /src/docker/build-in-container.sh "$@"
printf 'Boot images prepared. On the host run: sudo %q %q\n' \
  "$SOURCE_DIR/03-initialize-workspace.sh" "$BUILD_DIR/out"
