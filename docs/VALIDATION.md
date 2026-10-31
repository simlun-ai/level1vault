# Validation and acceptance

[Documentation index](README.md)

## Current revision

Historical results below do **not** certify the current source revision. Record the commit, build/release identity, environment and result for each acceptance run. A passing source test or ISO inspection is not a successful boot, workspace unlock or physical-device test.

| Layer | Command or procedure | What it establishes |
| --- | --- | --- |
| Local | `make test` | Shell lint/syntax and unit/behavior tests, including refusal paths and retention policy |
| Containers | `make test-container` | Actual GPT/FAT/GRUB assembly, fresh exported identities, account/PAM login and shell configuration |
| Disposable ZFS environment | `make test-zfs LEVEL1VAULT_DISPOSABLE_TEST_VM=1` | Encrypted full/incremental replication, interruption/resumption, holds, retention, failover, home binding and OS partition preservation |
| Pair preparation | `make prepare-pairs N=3` | Full live build and inspection, three unique pairs, active-pair encryption, initial replica seeding and recovery-record export |
| CI checks | `make ci` | Ruff lint/formatting, strict Pyright, shell checks, all unit suites, container assembly/account tests and host export validation |
| Full build | `make build` then `make check-build` | Actual Debian ISO, matching packaged kernel/ZFS module, initramfs, shipped helpers and manual-only pool policy |
| Workstation QEMU | [Automated and manual checks](QEMU.md) | Boot, root autologin, persistent home, reboot reset, degraded writes and mirror rejoin on disposable copies |
| Physical devices | Checklist below | Intended hardware, actual USB transport and sustained device behavior |

`make ci` runs Ruff lint/formatting, strict Pyright, shell checks, the pipeline/runtime/stage unit suites, and container assembly/account tests through BuildKit Bake, followed by host export validation. Real ZFS integration and the full Debian build run in separate CI jobs; CI never launches VMs. Real ZFS integration requires an explicitly disposable environment, not the normal build workstation. No automated suite writes physical USB drives. See [development](DEVELOPMENT.md#local-tests-on-linux) for dependencies and commands.

## Workstation acceptance before permanent use

Use a freshly built, initialized active pair. Run `make test-qemu PAIR=1`; retain its `result.json`, scenario logs and the build revision. Follow the [shared manual procedure](../stages/04-test-pair/README.md) for diagnosis or manual execution. Those commands are generated from the same checks used by the runner.

Additionally verify the interactive session flow: root autologin, `start`, tmux exit versus detach, refusal to export with remaining simlun sessions, both Y/n prompts, and reattachment after a failed export. Saved home settings must survive reboot and an OS upgrade without being replaced by image defaults. [Session behavior](HOME.md) defines the expected results.

Test a prepared release with `make test-qemu QEMU_ARGS='--upgrade-release build/upgrade-test'`, following [upgrade acceptance](UPGRADES.md#workstation-qemu-acceptance). Confirm its source revision inside the guest. File readback alone does not establish boot success.

For multiple pairs, exercise [replication and rotation](PAIRS.md), interrupted full/incremental receives, independent offsite holds, fenced failover, explicit rebinding and refusal of divergent history. Preview and apply retention on disposable copies; verify held bases and user snapshots remain and the next incremental sync still succeeds. Numeric pair order must not be mistaken for checkpoint freshness.

## Physical acceptance

Use the intended UEFI computers with Secure Boot disabled and disposable test data initially:

- Boot A alone and B alone with clean firmware boot selections; confirm each uses its own OS and can read the workspace.
- Boot both members, unlock, write as UID/GID 1000, export and reboot; verify the files and persistent home.
- Write with one member deliberately absent, export, rejoin the stale member, wait for resilver, scrub, and require both members ONLINE with no errors. Verify the updated data from the formerly stale member alone.
- Check NIC/storage firmware, cold boot, minimum RAM and USB stability under sustained writes.
- Apply an OS upgrade from a separate workstation, then boot each stick independently and verify the release revision and workspace contents.
- Test unexpected power loss only with disposable data. A changed per-boot hostid may require a deliberate fenced import; use [recovery](RECOVERY.md).

Always export before moving workspace drives. Keep the running boot stick connected until shutdown. Record model/serials and results outside the pools.

## Recorded build and source checks

On **2026-10-31**, the operator also reported that all of the following commands passed:

| Passed command | Coverage established by the reported run |
| --- | --- |
| `make prepare-pairs N=3` | Built and inspected the live OS, assembled three pairs with distinct identities, initialized one encrypted active workspace and two replicas, populated the replicas through initial replication, and exported recovery records for all three pairs |
| `make test` | Passed shell lint/syntax and unit/behavior tests, including safety refusals and failure handling |
| `make test-container` | Passed real GPT/FAT/GRUB assembly, account/PAM login and shell configuration tests, plus exported-image identity and sparse-extraction checks |
| `make ci` | Passed Ruff lint/formatting, strict Pyright, shell checks, all unit suites, container assembly/account tests, and host export validation |

The separate `make test-qemu` pass below establishes actual guest boot, encrypted unlock, ownership/permissions, persistent home, root-overlay reset, degraded writes, mirror recovery/scrub, and clean export. Together these results cover provisioning through VM acceptance. Initial replica seeding is covered by pair preparation; subsequent operational sync, rotation and failover require separate acceptance.

Individual command revisions and logs were not supplied for the four commands above; the QEMU checkout revision below applies specifically to that QEMU run.

## Recorded workstation QEMU acceptance

On **2026-10-31**, the operator reported a successful `make test-qemu` run for pair 1 on `simlun-x1`, using QEMU/KVM (the default accelerator). The runner and shared guest checks were from [commit `36b8764`](https://github.com/simlun-ai/level1vault/commit/36b876416c872e2ac9bcd5237a3b9499f324dfab).

All five scenarios passed: `both-reboot`, `a-alone`, `rejoin-a`, `b-alone`, and `rejoin-b`. This establishes UEFI boot and root autologin, workspace and home persistence across reboot, ephemeral root-overlay reset, independent member boots, degraded writes from each member, and mirror rejoin/resilver and scrub checks on the disposable virtio disk copies. The runner finished with `QEMU acceptance passed; original images were not modified.`

The reported results directory was:

```text
/home/simlun/Dev/level1vault/build/default/validation/20261031T202844Z-0b9fb58e
```

Evidence available here is the operator's terminal transcript; the successful run's `result.json` and scenario logs were not supplied. The source revision embedded in the previously built images was not recorded in that transcript, so `36b8764` identifies the test checkout, not a verified image build revision. This run does not establish physical USB acceptance, OS-upgrade acceptance, or the separate interactive `start`/tmux checks, and does not certify later revisions.

## Remaining acceptance

The 2026-10-31 reports leave these checks to complete for the intended deployment:

- **Physical USB:** run whole-image writing and readback verification on the intended drives, including the new pre-write metadata cleanup; boot each member and exercise the [physical acceptance checklist](#physical-acceptance). Virtio QEMU tests do not exercise USB transport or the physical writer.
- **Interactive sessions:** exercise `start`, tmux exit/detach/reattach, both Y/n prompts, and refusal to export with remaining sessions or jobs. Follow [home and session behavior](HOME.md).
- **OS upgrades:** run QEMU acceptance with `--upgrade-release`, confirm the guest release revision, and test the physical update procedure while preserving workspace data and saved home settings. Follow [upgrade acceptance](UPGRADES.md#workstation-qemu-acceptance).
- **Multi-pair operation:** test later incremental sync and rotation, interrupted receives, independent holds, retention, fenced failover and rebinding on disposable data. Three-pair preparation establishes initial replica seeding; the [operational workflow](PAIRS.md) and current [disposable ZFS integration suite](#current-revision) provide separate coverage.

## Historical evidence

These records describe earlier implementations, not acceptance of subsequent changes:

- [Full Debian Live build, 2026-10-14](https://github.com/simlun-ai/level1vault/actions/runs/38636756155): actual ISO and default images, kernel/module inspection, initramfs and image identities. This predates the BuildKit and persistent-home changes.
- [Real ZFS integration on Ubuntu 22.04](https://github.com/simlun-ai/level1vault/actions/runs/38711305462): disposable file-backed mirrors, encrypted replication/resumption, independent holds and numbered failover. It is not a retention-command or physical-USB result.
- Earlier operator reports recorded a normal QEMU boot/mount/reboot on 2026-10-15 and basic independent physical-stick boots plus both-member mirror operation. Physical degraded-write/rejoin and current session/upgrade acceptance were not established by those reports.

Keep new CI evidence with the corresponding PR/commit and workstation evidence with the tested build. Do not carry a historical pass forward as a current acceptance result.
