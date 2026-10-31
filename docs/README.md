# Documentation

Start with the [quick start](../README.md) to build and use the first pair. These pages hold the operational and technical details.

## Pipeline stages

| Stage | Input → output | Environment |
| --- | --- | --- |
| [1. Build live system](../stages/01-build-live/README.md) | Configuration + runtime → ISO and build records | BuildKit |
| [2. Assemble a pair](../stages/02-assemble-pair/README.md) | ISO → two bootable prepared images | BuildKit |
| [3. Initialize workspace](../stages/03-initialize-pair/README.md) | Prepared images + identity/passphrase → initialized pair | Host ZFS |
| [4. Test a pair](../stages/04-test-pair/README.md) | Initialized active pair → acceptance logs/report | Workstation QEMU; optional |
| [5. Write USB drives](../stages/05-write-usb/README.md) | Initialized images + identified disks → physical sticks | Host; `make write-usb` |
| [6. Upgrade existing OS](../stages/06-upgrade-os/README.md) | Inspected payload + existing pair → updated OS, preserved ZFS | BuildKit preparation + host; `make prepare-upgrade`, then `make upgrade-os` |
| [7. Replace one member](../stages/07-replace-member/README.md) | Original membership + blank USB → bootable empty replacement, then restored mirror | BuildKit + workstation + live OS |

[Pipeline orchestration](../pipeline/README.md) connects the stages. [Runtime](../runtime/README.md) contains the installed commands; [ZFS integration](../integration-tests/zfs/README.md) tests their interactions with provisioning.

## Technical references

| Page | Contents |
| --- | --- |
| [Build](BUILD.md) | Linux host prerequisites, Docker boundary, initialization, artifacts, failed builds and reproducibility |
| [BuildKit](BUILDKIT.md) | Dockerfile stages, artifact export, privileges, cache, identities and storage |
| [OS upgrades](UPGRADES.md) | In-place updates, release reuse, retries and testing |
| [Writing USB drives](../stages/05-write-usb/README.md) | Device selection, flashing, USB readback and GPT relocation |
| [QEMU testing](QEMU.md) | Firmware setup, guest checks and persistent writes |
| [Shared QEMU checks](../stages/04-test-pair/README.md) | Generated manual commands matching the automated boot, reboot, degraded-write and rejoin scenarios |
| [Persistent home and sessions](HOME.md) | Root autologin, start/tmux, encrypted home, defaults, export and upgrades |
| [Offline Git updates](GIT-BUNDLES.md) | Prepare full Git bundles and update encrypted working checkouts offline |
| [Offline transport](TRANSPORT.md) | Stage files on locked pairs and verify them into the encrypted inbox |
| [Pair operations](PAIRS.md) | Any number of pairs, encrypted replication, rotation, ordered failover and retention |
| [Recovery](RECOVERY.md) | Fenced imports, degraded mirrors and replacement members |
| [Development](DEVELOPMENT.md) | Make targets, local tests, CI, cleanup and repository layout |
| [Architecture](ARCHITECTURE.md) | Live root, boot layout, DKMS, host/container responsibilities and design review |
| [Validation](VALIDATION.md) | Current acceptance checklist and explicitly historical results |


- [Generated output: what to keep and what to delete](ARTIFACTS.md)

- [YubiKey HSM and pass custody](HSM.md) — offline installation, key storage, pipelines and acceptance.
