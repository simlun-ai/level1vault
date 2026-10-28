#!/usr/bin/env python3
"""Serialize cleanup against checkout operations and run builds as the caller."""

import fcntl
import hashlib
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git_path(name):
    path = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--git-path", name], cwd=ROOT, text=True
        ).strip()
    )
    return path if path.is_absolute() else ROOT / path


def builder_name():
    return "level1vault-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]


def user_command(args):
    # Privileged host coordinators hand all BuildKit work back to the sudo caller.
    uid = os.environ.get("SUDO_UID")
    if os.geteuid() == 0 and uid and int(uid) != 0:
        settings = [
            f"{key}={os.environ[key]}"
            for key in (
                "PAIR",
                "BUILDX_BUILDER",
                "DEBIAN_IMAGE",
                "FRESH",
                "LEVEL1VAULT_LOCKED",
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "DOCKER_CONFIG",
            )
            if key in os.environ
        ]
        return ["sudo", "-H", "-u", "#" + uid, "env", *settings, *args]
    return args


def wait_command(args, env):
    with subprocess.Popen(args, env=env) as process:
        while True:
            try:
                return process.wait()
            except KeyboardInterrupt:
                # The terminal also signals the child; retain the lock until it exits.
                continue


def main():
    action, *args = sys.argv[1:]
    if action == "builder-name":
        print(builder_name())
        return 0
    if action == "user":
        return wait_command(user_command(args), os.environ)
    if action not in ("shared", "clean"):
        raise ValueError("Expected shared, clean, user or builder-name")

    # Keep one checkout lock held for the complete command, including host stages.
    with git_path("level1vault.lock").open("a") as lock:
        fcntl.flock(
            lock,
            (fcntl.LOCK_EX if action == "clean" else fcntl.LOCK_SH) | fcntl.LOCK_NB,
        )
        env = dict(os.environ, LEVEL1VAULT_LOCKED="1")
        if action == "clean":
            result = wait_command(
                [
                    *shlex.split(os.environ.get("SUDO", "sudo")),
                    "python3",
                    str(ROOT / "pipeline/clean.py"),
                    *args,
                ],
                env,
            )
            if result:
                return result
            return wait_command(
                ["bash", str(ROOT / "pipeline/buildkit.sh"), "clean"], env
            )
        return wait_command(args, env)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BlockingIOError:
        sys.exit("Cleanup refused: another checkout operation is in progress")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"Lifecycle command failed: {error}")
