#!/usr/bin/env bash
set -euo pipefail
# Real file-only image assembly with a tiny synthetic ISO. This does not boot it.
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
TASK_TMP=$(mktemp -d)
trap 'rm -rf -- "$TASK_TMP"' EXIT
mkdir -p "$TASK_TMP/root/usr/local/share/level1vault" "$TASK_TMP/iso/live" "$TASK_TMP/iso/.disk"
printf '6.12.0-test-amd64\n' > "$TASK_TMP/root/usr/local/share/level1vault/kernel-version"
printf '2.3-test\n' > "$TASK_TMP/root/usr/local/share/level1vault/zfsutils-version"
printf 'zfsutils-linux\t2.3-test\n' > "$TASK_TMP/root/usr/local/share/level1vault/packages.tsv"
mksquashfs "$TASK_TMP/root" "$TASK_TMP/iso/live/filesystem.squashfs" -noappend -processors 1 >/dev/null
printf 'synthetic-kernel\n' > "$TASK_TMP/iso/live/vmlinuz-6.12.0-test-amd64"
printf 'synthetic-initrd\n' > "$TASK_TMP/iso/live/initrd.img-6.12.0-test-amd64"
printf 'test-marker\n' > "$TASK_TMP/iso/.disk/live-uuid-test"
xorriso -as mkisofs -R -o "$TASK_TMP/input.iso" "$TASK_TMP/iso" >/dev/null 2>&1
IMAGE_MIB=512 ESP_MIB=64 "$SOURCE_DIR/02-assemble-images.sh" "$TASK_TMP/input.iso" "$TASK_TMP/out"
test -f "$TASK_TMP/out/IMAGES-PREPARED"
test ! -e "$TASK_TMP/out/READY"
(cd "$TASK_TMP/out" && sha256sum -c PREPARED-SHA256SUMS)
for name in a b; do
  img="$TASK_TMP/out/usb-$name.img"
  # mtools opens the FAT partition directly at its 1 MiB byte offset.
  mcopy -i "$img@@1048576" ::/live/vmlinuz "$TASK_TMP/kernel-$name"
  cmp "$TASK_TMP/kernel-$name" "$TASK_TMP/iso/live/vmlinuz-6.12.0-test-amd64"
  mcopy -i "$img@@1048576" ::/EFI/BOOT/BOOTX64.EFI "$TASK_TMP/boot-$name.efi"
  file "$TASK_TMP/boot-$name.efi" | grep -q 'PE32+'
  mcopy -i "$img@@1048576" ::/.disk/live-uuid-test "$TASK_TMP/marker-$name"
  cmp "$TASK_TMP/marker-$name" "$TASK_TMP/iso/.disk/live-uuid-test"
  mcopy -i "$img@@1048576" ::/boot/grub/grub.cfg "$TASK_TMP/grub-$name.cfg"
done
python3 - "$TASK_TMP" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
m = json.loads((p/'out/images.json').read_text())
assert m['images']['a']['esp_uuid'] != m['images']['b']['esp_uuid']
assert m['images']['a']['zfs_partuuid'] != m['images']['b']['zfs_partuuid']
for name in ('a', 'b'):
    uuid = m['images'][name]['esp_uuid']
    assert uuid in (p/f'grub-{name}.cfg').read_text()
    with (p/f'out/usb-{name}.img').open('rb') as f:
        # First 256 KiB of the data partition remains blank (no ZFS labels).
        f.seek(65*1024*1024)
        assert f.read(256*1024) == bytes(256*1024)
PY
# Existing output must be rejected, preserving hashes.
if IMAGE_MIB=512 ESP_MIB=64 "$SOURCE_DIR/02-assemble-images.sh" "$TASK_TMP/input.iso" "$TASK_TMP/out"; then
  echo 'ERROR: overwrote existing output' >&2; exit 1
fi
(cd "$TASK_TMP/out" && sha256sum -c PREPARED-SHA256SUMS)
