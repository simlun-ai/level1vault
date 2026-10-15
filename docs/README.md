# Documentation

Start with the [quick start](../README.md) to build and use the first pair. These pages hold the operational and technical details.

| Page | Contents |
| --- | --- |
| [Build](BUILD.md) | Linux host prerequisites, Docker boundary, initialization, artifacts, failed builds and reproducibility |
| [BuildKit](BUILDKIT.md) | Dockerfile stages, artifact export, privileges, cache, identities and storage |
| [Writing USB drives](USB.md) | Device selection, checksums, flashing and GPT relocation |
| [QEMU testing](QEMU.md) | Firmware setup, guest checks, persistent writes and checksum refresh |
| [Shared QEMU checks](QEMU-CHECKS.md) | Generated manual commands matching the automated boot, reboot, degraded-write and rejoin scenarios |
| [Pair operations](PAIRS.md) | Any number of pairs, encrypted replication, rotation, ordered failover and retention |
| [Recovery and updates](RECOVERY.md) | Safe imports/exports, degraded mirrors, replacement members and OS updates |
| [Development](DEVELOPMENT.md) | Make targets, local tests, CI, cleanup and repository layout |
| [Architecture](ARCHITECTURE.md) | Live root, boot layout, DKMS, host/container responsibilities and design review |
| [Validation](VALIDATION.md) | Recorded results, hardware status and remaining acceptance checks |
