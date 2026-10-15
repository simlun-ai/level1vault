# Stage 2: assemble a bootable pair

Input: the stage 1 live ISO. Output: `usb-a.img`, `usb-b.img`, `images.json`, `BUILD-INFO.txt`, `PREPARED-SHA256SUMS`, and `IMAGES-PREPARED` in a new pair directory. Workspace partitions are empty.

`assemble.sh ISO NEW_OUTPUT_DIRECTORY` creates GPT images and independently bootable EFI filesystems using file operations inside the sandboxed BuildKit stage. Every assembly has fresh disk identities.

`make build` assembles the first pair. To prepare another pair from the existing ISO, run:

```bash
make assemble PAIR=2
```

The default output is `build/default/pairs/2`. Override `BUILD_DIR`, `PAIR`, or `OUT` as needed. An existing output directory is refused. If assembly fails, inspect the incomplete output and retry in a new directory; do not initialize incomplete images.

`make test-container` runs `tests/test_assembly.sh` on a synthetic ISO and checks independent exported pair identities. This verifies file assembly, not guest boot.

See [BuildKit](../../docs/BUILDKIT.md) and [initialization](../03-initialize-pair/README.md).
