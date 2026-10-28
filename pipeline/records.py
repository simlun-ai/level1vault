#!/usr/bin/env python3
"""Export durable pair records or acknowledge a completed member replacement."""

import argparse
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from pair_records import accept_replacement, records_root, save_pair  # noqa: E402 - resolve shared helpers after configuring their path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("action", choices=("save", "accept"))
parser.add_argument("--out", type=Path)
parser.add_argument("--pair", required=True)
parser.add_argument("--replacement")
parser.add_argument("--confirmed", action="store_true")
args = parser.parse_args()
try:
    if args.action == "save":
        save_pair(args.out, args.pair, records_root())
    else:
        accept_replacement(records_root(), args.pair, args.replacement, args.confirmed)
except (
    ValueError,
    KeyError,
    TypeError,
    OSError,
    subprocess.CalledProcessError,
) as error:
    sys.exit(f"Record operation stopped: {error}")
