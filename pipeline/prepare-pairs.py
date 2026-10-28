#!/usr/bin/env python3
"""Build and initialize a complete numbered set of ready-to-write USB pairs."""

import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Host helpers must not leave root-owned bytecode in the checkout.
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
from artifact_owner import publish  # noqa: E402 - resolve shared helpers after configuring their path


def command(*args, **kwargs):
    subprocess.run(list(map(str, args)), check=True, **kwargs)


def preflight(build, count):
    # Reject invalid requests and missing prerequisites before asking for secrets.
    if not re.fullmatch(r"[1-9][0-9]*", count):
        raise ValueError("N must be a positive integer without leading zeros")
    if build.exists() or build.is_symlink() or "," in str(build):
        raise ValueError(
            "Use a new BUILD_DIR without commas; existing runs are never overwritten"
        )
    if os.geteuid() != 0:
        raise ValueError("Run through make prepare-pairs with sudo on the host")
    for tool in (
        "git",
        "docker",
        "zpool",
        "zfs",
        "modprobe",
        "losetup",
        "udevadm",
        "blkid",
        "wipefs",
        "sfdisk",
        "zgenhostid",
        "flock",
        "realpath",
        "mktemp",
        "chown",
        "chmod",
    ):
        if shutil.which(tool) is None:
            raise ValueError(f"Missing host tool: {tool}")
    shallow = subprocess.check_output(
        [
            "git",
            "-c",
            "safe.directory=" + str(ROOT),
            "rev-parse",
            "--is-shallow-repository",
        ],
        cwd=ROOT,
        text=True,
    ).strip()
    if shallow != "false":
        raise ValueError("Fetch full history first: git fetch --unshallow --tags")
    command(
        sys.executable,
        ROOT / "pipeline/lifecycle.py",
        "user",
        "docker",
        "info",
        stdout=subprocess.DEVNULL,
    )
    command("modprobe", "zfs")
    pools = subprocess.check_output(
        ["zpool", "list", "-H", "-o", "name"], text=True
    ).splitlines()
    if any(f"level1-workspace-{pair}" in pools for pair in range(1, int(count) + 1)):
        raise ValueError(
            "A requested workspace pool is already imported; resolve ownership first"
        )


def ask_passphrase():
    # Collect and confirm the encryption secret before any long-running build.
    if not sys.stdin.isatty():
        raise ValueError("Use a terminal for the upfront passphrase prompt")
    first = getpass.getpass("New workspace passphrase (not saved): ")
    second = getpass.getpass("Confirm workspace passphrase: ")
    if first != second:
        raise ValueError("Passphrases do not match")
    if not 8 <= len(first.encode()) <= 512 or "\n" in first or "\r" in first:
        raise ValueError("Use a single-line passphrase of 8–512 UTF-8 bytes")
    return first


def prepare(build, count, passphrase):
    # Build and inspect the live OS once, then assemble the remaining unique pairs.
    env = dict(os.environ, PAIR="1")
    wrapper = ROOT / "pipeline/buildkit.sh"
    command("bash", wrapper, "build", build, env=env, stdin=subprocess.DEVNULL)
    marker = build / "PREPARING-PAIRS"
    marker.touch()
    publish(marker)
    command(
        "bash",
        wrapper,
        "check",
        build,
        build / "pairs/1",
        env=env,
        stdin=subprocess.DEVNULL,
    )
    for pair in range(2, count + 1):
        command(
            "bash",
            wrapper,
            "assemble",
            build,
            build / "pairs" / str(pair),
            env=env,
            stdin=subprocess.DEVNULL,
        )

    # Initialize one active pair and empty replicas without further prompts.
    for pair in range(1, count + 1):
        args = [
            "bash",
            ROOT / "stages/03-initialize-pair/initialize.sh",
            build / "pairs" / str(pair),
            str(pair),
        ]
        if pair == 1:
            command(*args, "--active", input=passphrase + "\n", text=True)
        else:
            command(*args, stdin=subprocess.DEVNULL)

    # Populate the replicas and publish readiness after cleanup.
    command(
        sys.executable,
        ROOT / "stages/03-initialize-pair/seed.py",
        build,
        str(count),
        stdin=subprocess.DEVNULL,
    )
    for pair in range(1, count + 1):
        command(
            sys.executable,
            ROOT / "pipeline/records.py",
            "save",
            "--out",
            build / "pairs" / str(pair),
            "--pair",
            str(pair),
            stdin=subprocess.DEVNULL,
        )
    marker.unlink()
    print(
        f"All {count} pairs are initialized, replicated and ready under {build / 'pairs'}."
    )
    print(
        "Next: identify USB disks with lsblk, then make write-usb PAIR=N DISK_A=... DISK_B=..."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--count", required=True)
    args = parser.parse_args()
    if args.build_dir.is_symlink():
        raise ValueError("Use a new BUILD_DIR, not a symlink")
    build = args.build_dir.resolve()
    preflight(build, args.count)
    passphrase = ask_passphrase()
    try:
        prepare(build, int(args.count), passphrase)
    finally:
        passphrase = None


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(
            f"Pair preparation stopped: {error}. Preserve partial outputs and inspect pools/loops before retrying in a new BUILD_DIR."
        )
    except KeyboardInterrupt:
        sys.exit(
            "Pair preparation interrupted; inspect partial outputs and pools/loops before recovery."
        )
