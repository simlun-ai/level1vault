#!/usr/bin/env bash
set -euo pipefail
# Compatibility entrypoint; Make and CI use the same BuildKit driver.

# Run the shared build entrypoint.
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BUILD_DIR=${1:-$SOURCE_DIR/build}
if [[ $# -gt 0 ]]; then shift; fi
exec bash "$SOURCE_DIR/scripts/buildkit.sh" build "$BUILD_DIR" "$@"
