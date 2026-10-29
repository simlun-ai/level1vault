#!/usr/bin/env python3
"""Prepare one full repository bundle for the independent transport staging command."""

import argparse
import fcntl
import subprocess
import sys
from pathlib import Path
from typing import cast

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
from git_bundles import add_bundle  # noqa: E402 - resolve shared helpers after configuring their path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    lock_path = subprocess.check_output(
        ["git", "rev-parse", "--git-path", "level1vault-git-bundles.lock"],
        cwd=ROOT,
        text=True,
    ).strip()
    lock = Path(lock_path)
    if not lock.is_absolute():
        lock = ROOT / lock
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        result = add_bundle(cast("str", args.repo), ROOT / "transport/git-bundles")
    print(f"Prepared {result}. Run make stage-transport PAIR=N when ready.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"Git bundle preparation stopped: {error}")
