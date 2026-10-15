# Stage 3: initialize the workspace

Input: a stage 2 prepared pair, a positive numeric pair ID, and the intended initial active/replica state. An active pair also needs a passphrase on stdin. Output: the same images with initialized mirror members, `WORKSPACE-INFO.txt`, `SHA256SUMS`, and `READY`.

Run on the host with working native ZFS:

```bash
make initialize PAIR=1 ACTIVE=1   # first writable pair; enter a new passphrase
make verify PAIR=1
make initialize PAIR=2            # empty replica prepared by stage 2
```

`initialize.sh` intentionally modifies the images in place. It does not write physical disks. `verify.sh` checks completion markers and final image hashes. Runtime import/mount remains manual.

On failure, `INITIALIZING` remains. Inspect the pool, loops, and logs before recovery; failed exports retain loop devices. Do not rerun over a partially initialized pair. See [build troubleshooting](../../docs/BUILD.md#initialize-a-pair).

`tests/` covers mocked initialization, identity checks, and cleanup failures through `make test`. Cross-stage real ZFS tests live in `../../integration-tests/zfs/` and require the explicit disposable-environment flag. See [pair operations](../../docs/PAIRS.md).
