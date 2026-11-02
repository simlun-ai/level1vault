#!/usr/bin/env bash
set -euo pipefail

# Verify Stockholm's winter and summer offsets using the installed timezone data.
[[ $(readlink /etc/localtime) == /usr/share/zoneinfo/Europe/Stockholm ]]
grep -qx Europe/Stockholm /etc/timezone
[[ $(env -u TZ date -d '2026-01-15 12:00:00 UTC' '+%z %Z') == '+0100 CET' ]]
[[ $(env -u TZ date -d '2026-07-15 12:00:00 UTC' '+%z %Z') == '+0200 CEST' ]]

# Check the generated locale and the environment of both login accounts.
[[ $(LC_ALL=en_US.UTF-8 locale charmap) == UTF-8 ]]
grep -qx 'LANG=en_US.UTF-8' /etc/default/locale
if grep -q '^LC_ALL=' /etc/default/locale; then
    echo 'LC_ALL must remain unset' >&2
    exit 1
fi
for account in root simlun; do
    # The login shell must expand its own locale environment.
    # shellcheck disable=SC2016
    su - "$account" -c 'test "$LANG" = en_US.UTF-8 && test "$(locale charmap)" = UTF-8'
done

# Check the US keyboard policy and the cached console keymap without loading it.
grep -qx 'XKBLAYOUT="us"' /etc/default/keyboard
grep -qx 'XKBVARIANT=""' /etc/default/keyboard
# Image inspection has no /dev/fd, so avoid process substitution here.
shopt -s nullglob
keymaps=(/etc/console-setup/cached*.kmap.gz)
[[ ${#keymaps[@]} -gt 0 ]]
gzip -t "${keymaps[@]}"
systemctl is-enabled keyboard-setup.service | grep -qx enabled
systemctl is-enabled console-setup.service | grep -qx enabled
