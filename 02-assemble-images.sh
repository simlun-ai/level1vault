#!/usr/bin/env bash
set -euo pipefail
# File-only assembly: no root, loop devices, mounts, udev, or ZFS module required.
[[ $# -eq 2 ]] || { echo "Usage: $0 ISO NEW_OUTPUT_DIRECTORY" >&2; exit 2; }
for cmd in xorriso unsquashfs sgdisk mkfs.vfat mcopy grub-mkstandalone \
  blkid python3 realpath truncate sha256sum stat cp dd mktemp; do
  command -v "$cmd" >/dev/null || { echo "Missing command: $cmd" >&2; exit 1; }
done
ISO=$(realpath -- "$1")
OUT_DIR=$(realpath -m -- "$2")
[[ -f $ISO && ! -e $OUT_DIR ]] || { echo 'ISO missing or output already exists' >&2; exit 1; }
IMAGE_MIB=${IMAGE_MIB:-12288}
ESP_MIB=${ESP_MIB:-4096}
[[ $IMAGE_MIB =~ ^[1-9][0-9]*$ && $ESP_MIB =~ ^[1-9][0-9]*$ ]] || exit 2
(( ESP_MIB >= 64 && IMAGE_MIB >= ESP_MIB + 128 )) || { echo 'Image/ESP too small' >&2; exit 2; }
mkdir -- "$OUT_DIR"
TMP=$(mktemp -d "$OUT_DIR/.assembly.XXXXXXXX")
cleanup() {
  local rc=$?
  trap - EXIT
  rm -rf -- "$TMP"
  if [[ $rc != 0 ]]; then
    rm -f "$OUT_DIR/IMAGES-PREPARED"
    echo "Assembly failed; do not use incomplete output: $OUT_DIR" >&2
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
xorriso -osirrox on -indev "$ISO" -extract / "$TMP/iso"
SQUASH="$TMP/iso/live/filesystem.squashfs"
[[ -f $SQUASH ]] || { echo 'Missing filesystem.squashfs' >&2; exit 1; }
KERNEL=$(unsquashfs -cat "$SQUASH" usr/local/share/level1vault/kernel-version)
TARGET_ZFS=$(unsquashfs -cat "$SQUASH" usr/local/share/level1vault/zfsutils-version)
[[ $KERNEL =~ ^[0-9][a-zA-Z0-9.+_-]*$ ]] || { echo 'Invalid kernel metadata' >&2; exit 1; }
VMLINUX="$TMP/iso/live/vmlinuz-$KERNEL"
INITRD="$TMP/iso/live/initrd.img-$KERNEL"
[[ -f $VMLINUX ]] || VMLINUX="$TMP/iso/live/vmlinuz"
[[ -f $INITRD ]] || INITRD="$TMP/iso/live/initrd.img"
[[ -f $VMLINUX && -f $INITRD ]] || { echo 'Missing selected kernel/initrd' >&2; exit 1; }
for file in "$SQUASH" "$VMLINUX" "$INITRD"; do
  [[ $(stat -Lc %s "$file") -lt 4294967296 ]] || { echo "FAT32 file too large: $file" >&2; exit 1; }
done
# Leave space for FAT tables, standalone GRUB and metadata. mcopy also checks capacity.
REQUIRED=$(( $(stat -Lc %s "$SQUASH") + $(stat -Lc %s "$VMLINUX") + $(stat -Lc %s "$INITRD") + 32*1024*1024 ))
(( REQUIRED < ESP_MIB*1024*1024 )) || { echo 'Payload does not fit ESP' >&2; exit 1; }
mkdir -p "$TMP/payload/live" "$TMP/payload/boot/grub" "$TMP/payload/EFI/BOOT"
cp -L -- "$VMLINUX" "$TMP/payload/live/vmlinuz"
cp -L -- "$INITRD" "$TMP/payload/live/initrd.img"
cp -- "$SQUASH" "$TMP/payload/live/filesystem.squashfs"
unsquashfs -cat "$SQUASH" usr/local/share/level1vault/packages.tsv > "$TMP/payload/live/packages.tsv"
if [[ -d $TMP/iso/.disk ]]; then cp -rL -- "$TMP/iso/.disk" "$TMP/payload/"; fi
for name in a b; do
  img="$OUT_DIR/usb-$name.img"
  truncate -s "${IMAGE_MIB}M" "$img"
  sgdisk --clear --new="1:2048:+${ESP_MIB}M" --typecode=1:ef00 --change-name=1:EFI \
    --new=2:0:0 --typecode=2:bf01 --change-name=2:WORKSPACE "$img"
  partuuid=$(LC_ALL=C sgdisk -i 2 "$img" | awk '/Partition unique GUID:/ {print tolower($4)}')
  [[ $partuuid =~ ^[0-9a-f-]{36}$ ]] || { echo 'Missing partition UUID' >&2; exit 1; }
  printf '%s\n' "$partuuid" > "$TMP/partuuid-$name"
  esp="$TMP/esp-$name.img"
  truncate -s "${ESP_MIB}M" "$esp"
  # Explicit random volume IDs also avoid collisions in builds within one second.
  volid=$(python3 -c 'import secrets; print(secrets.token_hex(4))')
  mkfs.vfat -F 32 -i "$volid" -n "LIVE_${name^^}" "$esp"
  uuid=$(blkid -p -s UUID -o value "$esp")
  printf '%s\n' "$uuid" > "$TMP/uuid-$name"
  cat > "$TMP/payload/boot/grub/grub.cfg" <<CFG
set default=0
set timeout=3
menuentry 'level1vault Debian Live (stateless)' {
    search --no-floppy --fs-uuid --set=root $uuid
    linux /live/vmlinuz boot=live components nopersistence noeject hostname=level1vault live-media=/dev/disk/by-uuid/$uuid live-media-timeout=30
    initrd /live/initrd.img
}
CFG
  cat > "$TMP/embedded.cfg" <<CFG
search --no-floppy --fs-uuid --set=root $uuid
set prefix=(\$root)/boot/grub
configfile /boot/grub/grub.cfg
CFG
  grub-mkstandalone --format=x86_64-efi \
    --modules='part_gpt fat search search_fs_uuid normal configfile linux' \
    --locales='' --fonts='' --output="$TMP/payload/EFI/BOOT/BOOTX64.EFI" \
    "boot/grub/grub.cfg=$TMP/embedded.cfg"
  for dir in live boot EFI; do mcopy -s -i "$esp" "$TMP/payload/$dir" ::/; done
  if [[ -d $TMP/payload/.disk ]]; then mcopy -s -i "$esp" "$TMP/payload/.disk" ::/; fi
  dd if="$esp" of="$img" bs=1M seek=1 conv=notrunc,sparse status=none
  sgdisk -v "$img"
done
python3 - "$OUT_DIR" "$TMP" "$IMAGE_MIB" "$ESP_MIB" "$KERNEL" "$TARGET_ZFS" <<'PY'
import json, pathlib, sys
out, tmp = map(pathlib.Path, sys.argv[1:3])
images = {n: {'file': f'usb-{n}.img', 'esp_uuid': (tmp/f'uuid-{n}').read_text().strip(),
              'zfs_partuuid': (tmp/f'partuuid-{n}').read_text().strip()} for n in ('a', 'b')}
assert images['a']['esp_uuid'] != images['b']['esp_uuid'], 'Duplicate ESP UUID'
assert images['a']['zfs_partuuid'] != images['b']['zfs_partuuid'], 'Duplicate partition UUID'
(out/'images.json').write_text(json.dumps({'format': 1, 'image_mib': int(sys.argv[3]),
    'esp_mib': int(sys.argv[4]), 'kernel': sys.argv[5], 'target_zfs_package': sys.argv[6],
    'images': images}, indent=2)+'\n')
PY
(cd "$OUT_DIR" && sha256sum usb-a.img usb-b.img images.json > PREPARED-SHA256SUMS)
{ date -u +build_time=%FT%TZ; sha256sum "$ISO"; dpkg-query -W live-build grub-common grub-efi-amd64-bin; } > "$OUT_DIR/BUILD-INFO.txt"
touch "$OUT_DIR/IMAGES-PREPARED"
printf 'Boot images prepared in %s; ZFS partitions are empty. Run 03-initialize-workspace.sh on the host.\n' "$OUT_DIR"
