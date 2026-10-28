#!/usr/bin/env python3
"""Stage local transport files on a complete locked USB pair, then export it."""

import argparse
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
from transport import stage, workspace_lock  # noqa: E402 - resolve shared helpers after configuring their path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", required=True)
    parser.add_argument("--source", type=Path, default=ROOT / "transport")
    args = parser.parse_args()
    with workspace_lock():
        stage(args.pair, args.source, ROOT / "runtime/workspace-discover")


if __name__ == "__main__":
    try:
        main()
    except (
        RuntimeError,
        ValueError,
        OSError,
        EOFError,
        subprocess.CalledProcessError,
    ) as error:
        sys.exit(
            f"Transport staging stopped: {error}. Check pool/export status before disconnecting; rerun staging to replace an incomplete package."
        )
