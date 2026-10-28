#!/usr/bin/env bash
set -euo pipefail

# Run under buildkit.sh's caller identity, dedicated builder and lifecycle lock.
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$SOURCE_DIR"
: "${BUILDX_BUILDER:?Use make ci or make test-container}"
TASK_TMP=$(mktemp -d)
trap 'rm -rf -- "$TASK_TMP"' EXIT
export CI_RESULTS="$TASK_TMP/results" CI_ISO="$TASK_TMP/iso"
# Existing paths let Bake resolve the exact filesystem grants before exporting.
mkdir "$CI_RESULTS" "$CI_ISO"
export ASSEMBLY_ID_1 ASSEMBLY_ID_2
ASSEMBLY_ID_1=$(python3 -c 'import uuid; print(uuid.uuid4())')
ASSEMBLY_ID_2=$(python3 -c 'import uuid; print(uuid.uuid4())')
# Grant only the temporary output path outside the checkout to Bake's exporter.
bake=(docker buildx bake --builder "$BUILDX_BUILDER" --file docker-bake.hcl --progress=plain
  "--allow=fs.write=$CI_RESULTS")

# Run independent checks together and retain the synthetic ISO for real exports.
"${bake[@]}" "${1:-ci}"
test -f "$CI_RESULTS/assembly-passed"
mv "$CI_RESULTS/live-image-amd64.hybrid.iso" "$CI_ISO/"

# Exercise both local exporters and host sparse extraction with identical ISO input.
"${bake[@]}" "--allow=fs.read=$CI_ISO" exports
for pair in 1 2; do
  bash pipeline/pair-archive.sh unpack "$CI_RESULTS/export-$pair/pair.tar" "$TASK_TMP/pair-$pair"
done
python3 stages/02-assemble-pair/tests/check_exports.py "$TASK_TMP"
