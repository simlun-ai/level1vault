#!/usr/bin/env bash
set -euo pipefail

# Inspect the rebuilt live OS before exporting its upgrade payload.
bash /src/stages/01-build-live/inspect.sh /artifacts --iso-only
python3 /src/common/live_payload.py /artifacts/live-image-amd64.hybrid.iso /release

# Publish completion only after inspection and payload extraction succeed.
touch /release/UPGRADE-READY
