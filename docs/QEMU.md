# QEMU testing

[Documentation index](README.md)

## Automated workstation test

This is an opt-in workstation test, not a CI job. CI checks the runner's code and documentation consistency without launching a VM.

Run this after `make initialize PAIR=1 ACTIVE=1`, with an initialized **active** pair. Stop other VMs using the images and detach their host loop mappings first. Keep them idle until copying finishes. The runner verifies `SHA256SUMS`, rejects loop-attached sources, and copies both images with `qemu-img`'s normal source locking. It never imports a pool on the host, opens a physical disk, or writes the original images.

Install QEMU, its image tools, OVMF, mtools and Python pexpect using your distribution's package manager. For example, on Ubuntu:

```bash
sudo apt install qemu-system-x86 qemu-utils ovmf mtools python3-pexpect
make test-qemu PAIR=1
```

Run as your normal user with read access to the initialized images and access to `/dev/kvm`. Enter the workspace passphrase at the hidden prompt. It stays in the runner's memory during the suite and is sent to the guest's unlock prompt; it is not passed in command arguments or saved in logs. The complete unlock exchange is omitted from the serial log. For unattended test-only provisioning, `QEMU_ARGS=--passphrase-stdin` accepts one line on stdin; do not put a real passphrase in shell history or environment variables.

The suite boots both drives, checks manual-only import, account settings, encryption, ownership, persistence and RAM-overlay reset across reboot. It then boots A alone, writes while degraded, rejoins and scrubs; repeats for B alone; and verifies both writes survived. Every boot starts without an imported pool. Each VM shuts down after a successful export. These are the same [shared acceptance checks](../stages/04-test-pair/README.md) used by the manual procedure.

The test copies retain the real UEFI loaders and live system. Only their external GRUB configuration is changed to add a serial console; the originals and build configuration are untouched. Each scenario uses fresh OVMF variables, no network, and no host directory sharing. These are virtio-disk tests, not USB transport tests.

Allow space for another full pair (up to 24 GiB with default images, usually less due to sparse copying), plus 4 GiB guest RAM. Copies are deleted on completion, failure, or a handled interrupt; private logs and `result.json` remain under `BUILD_DIR/validation/<test-run>/`. An uncatchable kill or host crash can leave a `disks-*` directory there: stop its QEMU process before manually removing it. Logs can include pool and system metadata. Original image checksums do **not** need refreshing after this automated suite.

Matching standard OVMF `_4M` or unsuffixed files are detected automatically. Override both together if your distribution stores them elsewhere. KVM is the default; TCG is slower but works without KVM:

```bash
make test-qemu PAIR=1 OUT=build/default/pairs/1 QEMU_ARGS='--accel tcg --timeout 900'
make test-qemu PAIR=1 QEMU_ARGS='--code /path/OVMF_CODE.fd --vars /path/OVMF_VARS.fd'
```

A timeout or failed check fails the suite; inspect `result.json` and the relevant scenario log. A passing script-only unit test does not establish that a real VM boot passed.

## Manual workstation test

Never attach the images to QEMU while the host has their pool imported or their loop mappings active. Keep only one active owner of this mirror. From `build/default/pairs/1/`:

```bash
sudo cp /usr/share/OVMF/OVMF_VARS_4M.fd ./ovmf-test-vars.fd
sudo chown "$(id -u):$(id -g)" usb-a.img usb-b.img ovmf-test-vars.fd
qemu-system-x86_64 \
  -machine q35,accel=kvm -cpu host -m 4096 -smp 2 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file=./ovmf-test-vars.fd \
  -drive if=virtio,format=raw,file=./usb-a.img \
  -drive if=virtio,format=raw,file=./usb-b.img
```

Firmware paths vary by distribution. On installations that supply `OVMF_CODE.fd` and `OVMF_VARS.fd` instead of the `_4M` files, replace both paths with that matching pair. Check `ls /usr/share/OVMF` first. Run QEMU as your desktop user, without sudo, to avoid GTK authorization errors. KVM access was already available on the tested workstation; group changes are only necessary if /dev/kvm access is denied. Use `accel=tcg` and omit `-cpu host` if KVM is unavailable. Secure Boot must be disabled. The live console autologs in as `simlun` (UID 2000, primary GID 2000), with password `password` and passwordless sudo. `/usr/local/sbin` is included in the login PATH and sudo secure_path. The login MOTD displays a short workspace command guide from `/etc/motd`. No SSH server or remote authentication is configured.

For a short interactive smoke test, run the commands below inside the guest. For the **full acceptance sequence**, follow [Shared QEMU checks](../stages/04-test-pair/README.md): it contains the exact guest script and ordered commands used by automation, including each-drive-only degraded writes and rejoin scrubs. Use disposable images for those tests and keep the same copies throughout the sequence.

```bash
cat /proc/cmdline
findmnt -t overlay,squashfs,tmpfs
sudo modprobe zfs
sudo zpool list                    # workspace should not be imported yet
sudo workspace-mount 1              # enter the workspace passphrase
stat -c '%u:%g %a' /mnt/workspace # 2000:2000 750
sudo zpool status workspace-1        # ONLINE with both members present
printf 'persistent\n' > /mnt/workspace/check.txt # writable as simlun
printf 'ephemeral\n' | sudo tee /etc/reset-check
cd /
sudo workspace-umount
sudo reboot
```

After reboot `/etc/reset-check` must be absent; manually mount the workspace and confirm `check.txt` survives. Run `cd /` then `sudo workspace-umount` before shutting down. Also boot **A alone and B alone**, using fresh OVMF variables, and perform the degraded/rejoin tests in [validation checklist](VALIDATION.md). Virtio tests do not replace USB transport/hardware tests.

VM writes change the raw image hashes. After completing tests, with the pool exported, VM stopped and loops detached, record new hashes from the repository root:

```bash
(cd build/default/pairs/1 && sha256sum usb-a.img usb-b.img images.json | sudo tee SHA256SUMS)
```
