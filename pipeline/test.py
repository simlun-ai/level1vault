#!/usr/bin/env python3
"""Run the workstation-safe tests alongside each stage and runtime component."""

import argparse
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Select an owner without changing the default local test collection.
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "scope", nargs="?", choices=("all", "pipeline", "runtime", "stages"), default="all"
)
args = parser.parse_args()
directories = {
    "pipeline": [ROOT / "pipeline/tests"],
    "runtime": [ROOT / "runtime/tests"],
    "stages": sorted((ROOT / "stages").glob("*/tests")),
}
selected = [
    path
    for owner, paths in directories.items()
    if args.scope in ("all", owner)
    for path in paths
]

# Collect independent suites without requiring stage directory names to be Python packages.
suite = unittest.TestSuite()
for directory in selected:
    suite.addTests(unittest.TestLoader().discover(str(directory)))

# Report failures through the shared Make and CI entrypoint.
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
