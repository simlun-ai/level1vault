#!/usr/bin/env bash
set -euo pipefail

# Select the shell sources owned by the pipeline, stages, and runtime.
candidate_list=$(mktemp)
trap 'rm -f -- "$candidate_list"' EXIT
case ${1:-} in
  --source-tree)
    # Container inputs have already been filtered by the CI context allowlist.
    find pipeline stages runtime integration-tests -type f -print0 > "$candidate_list"
    ;;
  '') git ls-files -z --cached --others --exclude-standard -- pipeline stages runtime integration-tests > "$candidate_list" ;;
  *) echo 'Usage: lint.sh [--source-tree]' >&2; exit 2 ;;
esac
mapfile -d '' -t candidates < "$candidate_list"
files=()
for file in "${candidates[@]}"; do
  [[ -f $file ]] || continue
  read -r first < "$file" || true
  case "$first" in
    '#!/bin/sh'|'#!/usr/bin/env bash') files+=("$file");;
  esac
done

# Check shell lint and syntax with each file's declared interpreter.
shellcheck "${files[@]}"
for file in "${files[@]}"; do
  if head -n1 "$file" | grep -q bash; then
    bash -n "$file"
  else
    sh -n "$file"
  fi
done
