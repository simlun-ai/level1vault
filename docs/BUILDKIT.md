# BuildKit artifact pipeline

[Documentation index](README.md)

The Dockerfile produces files through `FROM scratch` targets and BuildKit's local exporter. It does not publish or load a runnable builder image. `make build` exports stage outputs under `BUILD_DIR/live/` and `BUILD_DIR/pairs/PAIR/`.

## Stages and boundaries

| Stage / target | Work | Elevated build step? |
| --- | --- | --- |
| `toolchain`, `source` | Debian Trixie tools and allowlisted source files | No |
| `live` | live-build, Debian target kernel/DKMS, ISO; remove the temporary chroot before committing the layer | `RUN --security=insecure` |
| `assembly` | File-only GPT/FAT/GRUB assembly from the live ISO | No |
| `artifacts` | Scratch stage exporting `live/` and `pairs/PAIR/` | No commands |
| `pair-assembly`, `pair-artifacts` | Assemble/export another pair from an existing ISO | No |
| `test-assembly`, `test-account`, `test-results` | Sandboxed assembly/login tests and scratch result export | No |
| `inspection`, `inspection-results` | Inspect an existing ISO; host wrapper separately checks prepared image hashes | No |

Live-build requires mounts inside its chroot, so moving it into a Dockerfile does not remove its privilege requirement. The wrapper creates a dedicated `docker-container` builder named `level1vault`, with the `security.insecure` daemon entitlement. Only full builds request `--allow security.insecure`; only the `live` RUN uses it. This is equivalent to the previous privileged container and requires trusted source. Pool creation, passphrases, loop devices and physical USB writing remain host operations.

No host `/dev`, `/lib/modules`, Docker socket or checkout/build bind mounts are supplied to Dockerfile steps. The `.dockerignore` allowlist prevents generated artifacts and initialized images entering the normal context. Full builds additionally receive a named `repository-input` context containing only a Git bundle and the build commit ID. This installs the full offline checkout without copying local Git configuration or untracked files. Additional-pair assembly receives a named context containing only the ISO. Inspection refuses initialized/in-progress images and checks their prepared hashes on the host; only the ISO enters its named context.

## Requirements and commands

Install Docker Engine with the Buildx plugin; verify `sudo docker buildx version`. The Dockerfile uses the `docker/dockerfile:1-labs` frontend for the privileged RUN. The wrapper uses named contexts, metadata export and stage-specific cache controls. CI installs current Buildx. An older distribution Docker package without Buildx is insufficient; see [Docker's installation instructions](https://docs.docker.com/engine/install/).

```bash
make builder                       # create/bootstrap the dedicated builder
make build                         # local export into a fresh build/default/
make assemble PAIR=2      # fresh identities from the existing ISO
make test-container                # real BuildKit tests, including two exports
make check-build                   # before host ZFS initialization
make cache-clean                   # prune unused cache on this builder only
```

`BUILDX_BUILDER=level1vault-dev` selects another dedicated instance. Names must begin with `level1vault`; existing instances must use the `docker-container` driver and must have been configured with the insecure entitlement for full builds. The wrapper never changes the globally selected builder. Use `SUDO=` if your user already has Docker access; exports belong to the client user running Buildx.

## Cache, identities and updates

The toolchain and live ISO are cacheable. A fresh output directory does not force package updates: unchanged inputs can reuse the ISO. To refresh the base image and rerun all package/build steps:

```bash
make build BUILD_DIR=build-next FRESH=1
```

Each wrapper build/assembly call generates a fresh `ASSEMBLY_ID` build argument, consumed only in the assembly stages. This forces GPT/PARTUUID/FAT identity generation to rerun while allowing the expensive ISO stage to stay cached. Never use raw cached `pair-artifacts` output as a new pair. The supported Make/wrapper entrypoints always supply the fresh ID.

`make test-container` forces the test stages to rerun. It also exports two small synthetic pairs through the real scratch/local-export path, checks their hashes, and verifies all ESP/data partition identities are distinct across both pairs.

BuildKit retains intermediate cache on its own Docker volume. `make cache-clean` prunes unused cache on the selected dedicated builder, including its cached ISO; it leaves host images and workspace data intact. `make clean` still handles transient directories from older builds, and is a no-op for current artifact-only output. Neither command removes initialized images. Do not share the dedicated builder with unrelated projects if you want its cache lifecycle to stay project-specific.

## Output, space and failures

A successful full export contains `live/live-image-amd64.hybrid.iso`, `live/live-build.log` and the prepared `pairs/PAIR/` images. The wrapper adds `build.log` (plain BuildKit output, including failures), `builder-info.txt` and `build-metadata.json` (successful solve metadata/provenance). No chroot is exported. BuildKit cannot export an unsuccessful stage's files; use the streamed host `build.log` for failed live-build diagnostics.

Allow at least 80 GiB free across Docker storage and output storage for a full build, with more for retained versions. Raw images are 24 GiB per pair; do not rely on sparse allocation surviving BuildKit layer/export processing. The full workflow must be tested on the intended filesystem and storage budget. Inspection transfers only the ISO to avoid another copy of the raw pair.

Successful output is not ready to flash until host initialization writes `READY`. An export interrupted after writing some files is incomplete; never initialize it unless the prepared checksum validation succeeds. The wrapper refuses existing output paths, including partial outputs, to avoid merging artifacts or overwriting initialized data. Preserve failed logs and retry in a fresh directory.

For reproducible releases, pin the Dockerfile frontend, BuildKit image, Debian base image and package snapshots; default tags and mirrors move. See [reproducibility](BUILD.md#reproducibility).

References: [local exporter](https://docs.docker.com/build/exporters/local-tar/), [privileged RUN](https://docs.docker.com/reference/dockerfile/#run---security), [Buildx build options](https://docs.docker.com/reference/cli/docker/buildx/build/).
