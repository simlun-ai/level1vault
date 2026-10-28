#!/usr/bin/env bash
set -euo pipefail

# Run under buildkit.sh's caller identity, dedicated builder and lifecycle lock.
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$SOURCE_DIR"
: "${BUILDX_BUILDER:?Use make ci or make test-container}"
TASK_TMP=$(mktemp -d)
trap 'rm -rf -- "$TASK_TMP"' EXIT
export CI_RESULTS="$TASK_TMP/results" CI_ISO="$TASK_TMP/iso"
export ASSEMBLY_ID_1 ASSEMBLY_ID_2
ASSEMBLY_ID_1=$(python3 -c 'import uuid; print(uuid.uuid4())')
ASSEMBLY_ID_2=$(python3 -c 'import uuid; print(uuid.uuid4())')
bake=(docker buildx bake --builder "$BUILDX_BUILDER" --file docker-bake.hcl --progress=plain)

# Run independent checks together and retain the synthetic ISO for real exports.
"${bake[@]}" "${1:-ci}"
test -f "$CI_RESULTS/assembly-passed"
mkdir "$CI_ISO"
mv "$CI_RESULTS/live-image-amd64.hybrid.iso" "$CI_ISO/"

# Exercise both local exporters and host sparse extraction with identical ISO input.
"${bake[@]}" exports
for pair in 1 2; do
  bash pipeline/pair-archive.sh unpack "$CI_RESULTS/export-$pair/pair.tar" "$TASK_TMP/pair-$pair"
done
python3 stages/02-assemble-pair/tests/check_exports.py "$TASK_TMP"
