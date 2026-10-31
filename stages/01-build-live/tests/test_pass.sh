#!/usr/bin/env bash
set -euo pipefail

# Use a disposable GnuPG identity and public key material, never a real store.
fixture=$(mktemp -d)
export GNUPGHOME="$fixture/gnupg"
export PASSWORD_STORE_DIR="$fixture/store"
cleanup() {
    gpgconf --kill gpg-agent || true
    rm -rf -- "$fixture"
}
trap cleanup EXIT
mkdir -m 0700 "$GNUPGHOME"
gpg --batch --pinentry-mode loopback --passphrase '' --quick-generate-key \
    'Disposable pass integration <test@example.invalid>' rsa2048 encr 1d >/dev/null 2>&1
pass init test@example.invalid >/dev/null

# Check the documented multiline pipe preserves the complete single-line value.
printf '%s\n' 000102030405060708090a0b0c0d0e0f10111213 |
    pass insert --multiline yubikey/hsm/golden-1 >/dev/null
check_saved() {
    pass show yubikey/hsm/golden-1 | python3 -c \
        'import sys; assert sys.stdin.buffer.read() == b"000102030405060708090a0b0c0d0e0f10111213\n"'
}
check_saved

# Refusal must preserve the original ciphertext; a missing producer must fail.
if printf 'n\n' | pass insert --multiline yubikey/hsm/golden-1 >/dev/null 2>&1; then
    echo 'Unexpected overwrite acceptance' >&2
    exit 1
fi
check_saved
if pass show yubikey/hsm/missing 2>/dev/null | python3 -c 'import sys; sys.stdin.buffer.read()'; then
    echo 'pipefail did not propagate retrieval failure' >&2
    exit 1
fi
printf '%s\n' 'PASS: encrypted insertion, retrieval and overwrite refusal with disposable fixtures.'
