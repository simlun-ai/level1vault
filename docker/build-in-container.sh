#!/usr/bin/env bash
set -euo pipefail

# Check build privileges and prepare the artifact directory.
[[ $EUID -eq 0 ]] || { echo 'live-build needs root inside BuildKit' >&2; exit 1; }
cd /build
mkdir /artifacts

# Configure the requested live system.
python3 - <<'PY'
import json, os, subprocess
args = json.loads(os.environ.get('LB_CONFIG_JSON', '[]'))
assert isinstance(args, list) and all(isinstance(x, str) for x in args)
subprocess.run(['/src/01-configure-live.sh', *args], check=True)
PY

# Build and retain the live ISO and build log.
lb build 2>&1 | tee /artifacts/live-build.log
cp /build/live-image-amd64.hybrid.iso /artifacts/

# Remove temporary live-build state.
lb clean --purge
# Never recursively delete a chroot while a filesystem is mounted beneath it.
python3 - <<'PY'
from pathlib import Path
for line in Path('/proc/self/mountinfo').read_text().splitlines():
    target = line.split()[4]
    if target == '/build' or target.startswith('/build/'):
        raise SystemExit('Refusing chroot cleanup with remaining mount: ' + target)
PY

# Remove the unmounted build directory.
cd /
rm -rf -- /build
mkdir /build
