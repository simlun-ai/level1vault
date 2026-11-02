# Offline clock synchronization

The live image includes `gpsd`, `chrony`, and `gpsd-clients` for a USB GPS receiver.
No internet or mounted workspace is required. Booting without a receiver remains
supported: the machine starts from its hardware clock (RTC), with no external
synchronization until GPS reception is established.

## Receiver and first use

The initial target is the Geekstory VK-162 G-Mouse (Amazon product B078Y52FGQ),
a complete receiver with a USB cable and integrated antenna. The automatic rule
matches u-blox 7 USB vendor/product `1546:01a7`, not the product name. Sellers can
change hardware: this identity and the actual unit's behavior still require
physical verification. Other USB serial devices are not automatically probed.
The receiver has no usable PPS output; synchronization uses serial time reports.

1. Boot the updated live image and connect one receiver, preferably before `start`.
2. Place it near a window or outside with a clear view of the sky. First acquisition
   can take several minutes. If reception is poor, reposition the receiver.
3. Run `cgps -s` to check reception and the reported date/time; press `q` to exit.
4. Run `workspace-clock-status`. Look for `#* GPS` in sources, a recent `LastRx`,
   a plausible reference date, and `Leap status : Normal` in tracking. The tracking
   system-time correction should settle before doing time-sensitive work.

GPS synchronization is automatic when valid reports arrive. Chrony may jump the
clock for errors over one second during its first three clock updates. These
updates may occur much later than boot if reception starts late. Connect and
synchronize before starting important work; later corrections are gradual.
There is no automatic workspace/session restart or blocking boot prompt.

## What is installed and configured

- `gpsd` continuously reads the matched receiver using `-n`. A device-bound
  `level1vault-gpsd@TTY.service` starts on connection and stops on removal.
  Its local diagnostic endpoint uses GPSD's default loopback binding.
- `chrony` reads GPSD's non-PPS shared-memory source, `SHM 0`. It has no configured
  internet servers, DHCP source directories, or NTP listening port. Chrony's
  monitoring interface is local to the machine. Debian's generic GPSD service,
  socket, hotplug service and `systemd-timesyncd` are masked to avoid competing
  readers or clock setters.
- The RTC is interpreted as UTC. `rtcsync` enables periodic kernel RTC updates
  (approximately every 11 minutes while synchronized), so a successful GPS fix
  does not mean the RTC was immediately written. After synchronization and once
  the correction has settled, run `sudo hwclock --systohc --utc` if shutting down
  before the periodic write. Local display time depends on the OS timezone.
- Drift estimates live in the temporary OS, not the encrypted workspace. They
  reset on reboot; the RTC retains its time across pair changes on the same
  computer. No per-computer calibration is persisted in this first version.

`workspace-clock-status` shows both clocks' displayed timescales (local and UTC
system time), chrony tracking, source selection and sample age. It does not set
anything. No GPS selection means GPS is not currently selected for synchronization;
a previously selected source and normal tracking do not prove current reception.
After unplugging, chrony continues in holdover until samples become stale and
keeps applying its learned frequency correction. Reconnect for a fresh reference.

## Diagnosis

```sh
workspace-clock-status
cgps -s
udevadm info --attribute-walk --name=/dev/ttyACM0
systemctl status level1vault-gpsd@ttyACM0.service chrony.service
journalctl -b -u level1vault-gpsd@ttyACM0.service -u chrony.service
```

Use the actual receiver TTY in place of `ttyACM0`. If there is no matching service,
inspect the USB identity with `udevadm` before changing the udev rule; do not
broaden it to every serial device. Only one receiver is supported at a time.

Serial message latency varies. Expect ordinary wall-clock usefulness, not a
precision time standard: the often quoted roughly 250 ms USB GPS accuracy is not
a measured guarantee for this receiver. No latency offset is configured until
measured against a trusted clock. During initial testing, reject implausible dates
rather than relying only on a reported satellite fix.

## Validation

`make test` covers command behavior and staged installation. `make test-container`
runs the actual clock hook and checks the real Debian chrony configuration parser,
service enablement and masks. `make check-build` repeats configuration checks on
the built image. These do not test satellite reception or actual clock correction.

Hardware acceptance is still required:

- Confirm the shipped unit's USB identity and correct NMEA UTC date/time.
- Boot both with and without GPS connected; verify no workspace mount is required.
- Acquire a fix, confirm GPS selection, and compare against a trusted clock.
- Unplug and reconnect; verify the service stops/restarts and fresh samples resume.
- Correct a deliberately inaccurate clock before starting a session; confirm the
  initial step and subsequent gradual correction behavior.
- Check the RTC after synchronization/write and after shutdown/reboot without GPS.

References: [GPSD time service](https://gpsd.io/gpsd-time-service-howto.html),
[chrony configuration](https://chrony-project.org/doc/4.6/chrony.conf.html).
