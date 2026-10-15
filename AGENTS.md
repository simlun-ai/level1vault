# Working on level1vault

## Readable code

- Separate meaningful phases with blank lines and a short, one-sentence comment above each section.
- Section comments state **what** the block accomplishes, not how its implementation works (for example, “Verify the received checkpoint.”).
- Keep related statements together; do not comment every line or split small, self-explanatory functions unnecessarily.
- Keep explanations of subtle safety constraints close to the relevant code. These may explain why a check is necessary.
- Keep readability-only changes separate from behavior changes, and preserve existing safety checks.

## Documentation and commands

- Keep the main README short: overview, build commands, USB writing, and everyday use after boot.
- Put detailed setup, design, recovery, and test guidance under `docs/`; maintain the contents in `docs/README.md`.
- Use the Makefile as the shared entrypoint for common local and CI lifecycle commands.
- Describe the host as a Linux amd64 workstation with working ZFS. Ubuntu is a tested example, not a requirement.
- Use `/dev/sda` and `/dev/sdb` as USB examples, with an explicit device-identification step before destructive writes.
- Keep automated QEMU checks and the manual QEMU procedure aligned; document which checks still require hardware or operator testing.

## Project constraints

- Build the stateless Debian Trixie live system and artifacts in BuildKit; initialize ZFS and write USB drives on the host.
- Keep workspace import and mounting explicit through `workspace-mount`; use `workspace-umount` to sync and export before disconnecting.
- Support arbitrary numerically ordered pairs named `workspace-N`; active/replica is state, not a permanent part of the name.
- Preserve passphrase encryption, UID/GID 2000 ownership, explicit fencing, and refusal to overwrite divergent history.
- Cleanup must preserve initialized images and workspace data and refuse resources still in use.

## Validation and delivery

- Run `make test` for script changes. Run relevant container, image, or ZFS integration checks when behavior changes.
- Keep real host ZFS integration tests restricted to explicitly enabled disposable VMs; never silently opt a workstation in.
- Distinguish passing mocks, actual builds, VM acceptance, and physical-device verification in reports.
- Do not claim a boot or hardware test passed unless it actually ran successfully.
