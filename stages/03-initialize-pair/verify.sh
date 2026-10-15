#!/usr/bin/env bash
set -euo pipefail

# Verify the initialized images before boot testing or USB writing.
OUT=${1:?Output directory required}
[[ -f $OUT/READY && ! -e $OUT/INITIALIZING ]] || { echo "Not ready: $OUT" >&2; exit 1; }
(cd "$OUT" && sha256sum -c SHA256SUMS)
