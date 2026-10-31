# YubiKey HSM and pass custody

Run key creation, token programming and verification inside the offline live OS,
as simlun after `start`. `pass` is assumed already configured, with its store
and GnuPG identity in the persistent home. Its decryption must work independently
of the HMAC slot being provisioned. Do not reconnect the live session to a network.
Physical-token and replica-retrieval acceptance remain pending.

## Prepare the tools offline

Transport the `homelab-hsm` repository using [Git bundles](GIT-BUNDLES.md).
For the homelab inventory runbook, transport `homelab` separately as well. Bundle
preparation and OS building happen on the online workstation before this session.
After `start 1`, import the staged delivery:

```sh
sudo workspace-transport-import 1
workspace-transport-import-git-bundles /mnt/workspace/inbox/<delivery>/git-bundles
```

Replace `<delivery>` with the directory reported by the import. Install the
committed Debian 13/Python 3.13 wheels without network access:

```sh
python3 -B /mnt/workspace/git/homelab-hsm/homelab_secrets/scripts/offline.py \
  install --venv /mnt/workspace/venvs/homelab-hsm
source /mnt/workspace/venvs/homelab-hsm/bin/activate
homelab-hsm --help
```

The installer verifies bundle/source consistency, hashes, dependencies and unit
tests. Rerun it after importing updated HSM code. Keep the environment outside
both imported repositories: Git import rejects generated files, even ignored ones.
No homelab submodule initialization is needed for token setup. `make`,
`python3-venv` and the restricted `yubikey` device group are supplied by the live
image. Run token commands without sudo. Firmware/serial/slot inspection is:

```sh
homelab-hsm inspect
```

Only one YubiKey should be attached. The image grants simlun access to the
inventory's OTP HID devices independently of the root console's active seat.
Unexpected products require deliberate USB-rule review; do not grant world access.

## Establish the stored generation once

The item `yubikey/hsm/golden-1` contains exactly one line: 40 hexadecimal
characters encoding 20 random bytes. Preserve leading zeros. The item path
identifies the generation; do not append notes or metadata to its plaintext.

In the actual tmux pane, point GnuPG's terminal pinentry at that terminal:

```sh
export GPG_TTY="$(tty)"
gpg-connect-agent updatestartuptty /bye
set -o pipefail
```

Use a local unrecorded terminal with shell tracing disabled. First check whether
the encrypted entry exists without decrypting or displaying it:

```sh
if test -e "${PASSWORD_STORE_DIR:-$HOME/.password-store}/yubikey/hsm/golden-1.gpg"; then
    printf '%s\n' 'golden-1 already exists: retrieve it; do not generate again.'
else
    homelab-hsm generate-key | pass insert --multiline yubikey/hsm/golden-1
fi
```

Run the complete guarded creation block deliberately, with only one creation
process at a time. **With piped stdin, pass can overwrite without prompting even
without `--force`.** Do not run the bare generation/insertion pipeline against an
existing item; the existence check above is required. Never use `--force`. Stop
on any failure. A failure to decrypt an
existing entry is a retrieval problem, not evidence that it is absent. Check the
configured store before creation; do not switch stores to bypass a failure.
The generator cannot know whether a generation already exists.

No plaintext enters a shell variable, command argument, clipboard or temporary
file. The pipe carries the key to `pass`, which persists encrypted ciphertext.
Never use `pass show` alone, command substitution, `tee`, or secret file redirects.
The HSM commands reject direct terminal/file secret streams, but cannot detect
indirect capture by another process.

## Program and verify from the saved entry

Always retrieve the saved key for programming; never connect the generator
directly to a token. Follow the HSM CLI's physical-label and touch prompts:

```sh
pass show yubikey/hsm/golden-1 | homelab-hsm program --label key0
pass show yubikey/hsm/golden-1 | homelab-hsm verify --label key0
```

Stop on any nonzero pipeline status. Empty or malformed input is rejected before
USB access. Confirmations use `/dev/tty` independently of the secret pipe. If slot
2 is occupied, verify first. Deliberate replacement uses `program --label key0
--replace` and a separate erase confirmation; slot 1 is always preserved.
After an interrupted write or mismatch, reinsert and verify with the same stored
key. Do not regenerate it. Keep that device marked unverified until it passes.

Repeat for the physical inventory. For the homelab's four labels:

```sh
pass show yubikey/hsm/golden-1 |
  homelab-hsm verify-set --labels key0 key1 key2 key3
```

The HSM project owns [command semantics and verification](https://github.com/simlun-ai/homelab-hsm/blob/main/homelab_secrets/SETUP.md).
The homelab project owns [its inventory and installation results](https://github.com/simlun-ai/homelab/blob/main/docs/installation/00-yubikey-setup.md).

## Replication and acceptance

Keep the encrypted pass store and the independently usable GnuPG decryption
material in the persistent home. Replicate using [the existing pair procedure](PAIRS.md),
then follow its read-only replica checks and verify that the saved item can be
retrieved and used to verify a token from the replica. An active mirror alone is
not an independent backup. Do not promote a replica merely to test retrieval.
Retain older generations while any records/backups depend on them.

Record only public dates, software revisions, model/firmware/serial mappings and
PASS/failure phases. Never save secret values or terminal transcripts. Acceptance
must exercise actual root autologin → `start` → simlun/tmux, group USB access,
GnuPG pinentry with a pipe, all four devices, touch enforcement and replica
retrieval. Unit tests and successful dependency installation do not prove these.
Exit all panes, export the workspace and shut down through `start`'s prompts.
