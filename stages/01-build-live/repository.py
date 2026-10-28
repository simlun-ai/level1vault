#!/usr/bin/env python3
"""Package committed repository history and install an independent offline checkout."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

ORIGIN = "https://github.com/simlun-ai/level1vault.git"


def git(*args: str | Path, cwd: Path | None = None):
    options = ["-c", "safe.directory=" + str(Path(cwd).resolve())] if cwd else []
    return subprocess.check_output(
        ["git", *options, *map(str, args)], cwd=cwd, text=True
    ).strip()


def bundle(source: Path, destination: Path):
    # Require complete history before preparing the image's repository input.
    if git("rev-parse", "--is-shallow-repository", cwd=source) != "false":
        raise SystemExit(
            "Full Git history is required; run git fetch --unshallow --tags and retry."
        )
    commit = git("rev-parse", "HEAD", cwd=source)
    destination.mkdir()
    git("bundle", "create", destination / "source.bundle", "--all", "HEAD", cwd=source)
    (destination / "commit").write_text(commit + "\n")


def install(source: Path, destination: Path):
    # Create a self-contained checkout at the build's committed revision.
    commit = (source / "commit").read_text().strip()
    git("clone", "--no-checkout", source / "source.bundle", destination)
    git("checkout", "--detach", commit, cwd=destination)
    git("remote", "set-url", "origin", ORIGIN, cwd=destination)

    # Verify that the installed history and worktree are complete and clean.
    assert git("rev-parse", "--is-shallow-repository", cwd=destination) == "false"
    git("fsck", "--full", cwd=destination)
    assert git("rev-parse", "HEAD", cwd=destination) == commit
    assert not git("status", "--porcelain", cwd=destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("bundle", "install"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    {"bundle": bundle, "install": install}[args.action](
        args.source.resolve(), args.destination.resolve()
    )
