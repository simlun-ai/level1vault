#!/usr/bin/env bash
set -euo pipefail

# Validate the installed policy without starting services or changing either clock.
chronyd -p -f /etc/chrony/chrony.conf > /dev/null
systemctl is-enabled chrony.service | grep -qx enabled
for unit in gpsd.service gpsd.socket gpsdctl@.service systemd-timesyncd.service; do
    [[ $(readlink "/etc/systemd/system/$unit") == /dev/null ]]
done
grep -qx 'UTC' /etc/adjtime
grep -Fq 'BindsTo=dev-%i.device' /etc/systemd/system/level1vault-gpsd@.service
grep -Fq 'ATTRS{idVendor}=="1546", ATTRS{idProduct}=="01a7"' /etc/udev/rules.d/99-level1vault-gps.rules
command -v gpsd
command -v cgps
command -v chronyc
