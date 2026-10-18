# Stage 3: initialize the workspace

Input: a stage 2 prepared pair, a positive numeric pair ID, and the intended initial active/replica state. An active pair also needs a passphrase on stdin. Output: the same images with initialized mirror members, `WORKSPACE-INFO.txt` and `READY`.

Run on the host with working native ZFS:

```bash
make initialize PAIR=1 ACTIVE=1   # first writable pair; enter a new passphrase
make initialize PAIR=2            # empty replica prepared by stage 2
```

`make prepare-pairs N=3` runs the complete initial pipeline with prompts up front. `seed.py` then attaches only the new image pairs, imports by recorded GUID through private device search paths, populates replicas using the runtime sync helper, exports its pools, detaches loops, and publishes READY. It never loads the encryption key for raw replication or changes unrelated host pools. A failed export retains its loops and leaves readiness unpublished. See [batch preparation](../../docs/BUILD.md#prepare-n-complete-pairs-in-one-command).

`initialize.sh` intentionally modifies the images in place. It does not write physical disks. Completion markers and identity checks guard subsequent operations. Runtime import/mount remains manual.

On failure, `INITIALIZING` remains. Inspect the pool, loops, and logs before recovery; failed exports retain loop devices. Do not rerun over a partially initialized pair. See [build troubleshooting](../../docs/BUILD.md#initialize-a-pair).

`tests/` covers mocked initialization, identity checks, and cleanup failures through `make test`. Cross-stage real ZFS tests live in `../../integration-tests/zfs/` and require the explicit disposable-environment flag. See [pair operations](../../docs/PAIRS.md).
