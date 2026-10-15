#!/usr/bin/env bash
set -euo pipefail

# Select the shell sources owned by the pipeline, stages, and runtime.
mapfile -t candidates < <(git ls-files --cached --others --exclude-standard -- pipeline stages runtime integration-tests)
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
