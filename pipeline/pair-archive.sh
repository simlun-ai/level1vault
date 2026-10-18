#!/usr/bin/env bash
set -euo pipefail

# Package sparse images before BuildKit commits or copies their filesystem layer.
case ${1:-} in
  pack)
    [[ $# == 3 ]] || exit 2
    mkdir -p -- "$(dirname -- "$3")"
    tar --create --sparse --format=pax --file="$3" --directory="$2" .
    ;;

  # Publish a prepared pair only after the entire archive has been extracted.
  unpack)
    [[ $# == 3 && ! -e $3 && ! -L $3 ]] || { echo 'Use a new pair output directory' >&2; exit 2; }
    mkdir -p -- "$(dirname -- "$3")"
    staging=$(mktemp -d "$(dirname -- "$3")/.pair-unpack.XXXXXXXX")
    trap 'rm -rf -- "$staging"' EXIT
    tar --extract --no-same-owner --file="$2" --directory="$staging"
    [[ -f $staging/IMAGES-PREPARED && ! -e $staging/READY && ! -e $staging/INITIALIZING ]]
    mv -T -- "$staging" "$3"
    ;;
  *) echo 'Usage: pair-archive.sh pack DIRECTORY ARCHIVE | unpack ARCHIVE NEW_DIRECTORY' >&2; exit 2 ;;
esac
