#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'live-build needs root inside the container' >&2; exit 1; }
[[ ! -e /build/config && ! -e /build/chroot && ! -e /build/out ]] || {
  echo 'Use a fresh build directory; inspect/clean failed chroot mounts before retrying' >&2; exit 1;
}
cd /build
/src/01-configure-live.sh "$@"
lb build 2>&1 | tee build.log
/src/02-assemble-images.sh /build/live-image-amd64.hybrid.iso /build/out
