# QEMU testing

[Documentation index](README.md)

Never attach the images to QEMU while the host has their pool imported or their loop mappings active. Keep only one active owner of this mirror. From `build/out/`:

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

On Ubuntu 22.04 installations that supply `OVMF_CODE.fd` and `OVMF_VARS.fd` instead of the `_4M` files, replace both paths with that matching pair. Check `ls /usr/share/OVMF` first. Run QEMU as your desktop user, without sudo, to avoid GTK authorization errors. KVM access was already available on the tested workstation; group changes are only necessary if /dev/kvm access is denied. Use `accel=tcg` and omit `-cpu host` if KVM is unavailable. Secure Boot must be disabled. The live console autologs in as `simlun` (UID 2000, primary GID 2000), with password `password` and passwordless sudo. `/usr/local/sbin` is included in the login PATH and sudo secure_path. The login MOTD displays `Hello, world` from `/etc/motd`. No SSH server or remote authentication is configured.

Inside the guest:

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
(cd build/out && sha256sum usb-a.img usb-b.img images.json | sudo tee SHA256SUMS)
```
