#!/usr/bin/env python3
"""Run the workstation-safe tests alongside each stage and runtime component."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

# Collect independent suites without requiring stage directory names to be Python packages.
suite = unittest.TestSuite()
for directory in [ROOT / 'pipeline/tests', ROOT / 'runtime/tests', *sorted((ROOT / 'stages').glob('*/tests'))]:
    suite.addTests(unittest.TestLoader().discover(str(directory)))

# Report failures through the shared Make and CI entrypoint.
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
