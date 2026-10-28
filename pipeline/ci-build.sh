#!/usr/bin/env bash
set -euo pipefail

# Build and inspect the real live system through the existing lifecycle commands.
make build
make check-build
bash pipeline/buildkit.sh release "$BUILD_DIR" "$BUILD_DIR/upgrade"
python3 - "$BUILD_DIR/upgrade" <<'PYRELEASE'
import sys
from pathlib import Path
sys.path.insert(0, 'stages/06-upgrade-os')
import upgrade
upgrade.load_release(Path(sys.argv[1]).resolve())
PYRELEASE

# Verify caller ownership and checkout cleanup while retaining release artifacts.
test -z "$(find "$BUILD_DIR" ! -uid "$(id -u)" -print -quit)"
builder=${BUILDX_BUILDER:-$(python3 pipeline/lifecycle.py builder-name)}
make clean
test -z "$(docker volume ls -q --filter "name=buildx_buildkit_${builder}0_state")"
test -f "$BUILD_DIR/upgrade/UPGRADE-READY"
