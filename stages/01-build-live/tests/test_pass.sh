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

# Guard before starting the producer: pass skips overwrite prompts on piped stdin.
create_once() {
    if test -e "$PASSWORD_STORE_DIR/yubikey/hsm/golden-1.gpg"; then
        return 0
    fi
    printf '%s\n' generated >> "$fixture/generation-count"
    printf '%s\n' 000102030405060708090a0b0c0d0e0f10111213 |
        pass insert --multiline yubikey/hsm/golden-1 >/dev/null
}
create_once
check_saved() {
    pass show yubikey/hsm/golden-1 | python3 -c \
        'import sys; assert sys.stdin.buffer.read() == b"000102030405060708090a0b0c0d0e0f10111213\n"'
}
check_saved

# Repeating the guarded creation must neither regenerate nor replace ciphertext.
cp "$PASSWORD_STORE_DIR/yubikey/hsm/golden-1.gpg" "$fixture/original.gpg"
create_once
cmp "$fixture/original.gpg" "$PASSWORD_STORE_DIR/yubikey/hsm/golden-1.gpg"
[[ $(wc -l < "$fixture/generation-count") -eq 1 ]]
check_saved

# A failed producer must fail the pipeline even when the consumer exits zero.
if pass show yubikey/hsm/missing 2>/dev/null | python3 -c 'import sys; sys.stdin.buffer.read()'; then
    echo 'pipefail did not propagate retrieval failure' >&2
    exit 1
fi
printf '%s\n' 'PASS: encrypted insertion, retrieval and guarded creation with disposable fixtures.'
