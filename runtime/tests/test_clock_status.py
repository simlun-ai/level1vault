"""Exercise clock diagnostics without changing host time or requiring GPS."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

COMMAND = Path(__file__).resolve().parents[1] / "workspace-clock-status"


class ClockStatusTests(unittest.TestCase):
    def test_reports_reference_and_sample_age_without_mutating_time(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            log = base / "calls"
            chronyc = base / "chronyc"
            chronyc.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CLOCK_LOG"\n'
                'case "$*" in\n'
                '  "-n tracking") echo "Leap status : Not synchronised" ;;\n'
                '  "-n sources -v") echo "#? GPS 0 3 0 - +0ns" ;;\n'
                "  *) exit 9 ;;\nesac\n"
            )
            chronyc.chmod(0o755)
            result = subprocess.run(
                [str(COMMAND)],
                env=dict(
                    os.environ,
                    PATH=f"{base}:{os.environ['PATH']}",
                    CLOCK_LOG=str(log),
                ),
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("Not synchronised", result.stdout)
            self.assertIn("LastRx", result.stdout)
            self.assertIn("holdover", result.stdout)
            self.assertEqual(log.read_text(), "-n tracking\n-n sources -v\n")

    def test_service_failure_is_not_reported_as_synchronization(self):
        with tempfile.TemporaryDirectory() as directory:
            chronyc = Path(directory) / "chronyc"
            chronyc.write_text('#!/bin/sh\necho "506 Cannot talk to daemon"\nexit 1\n')
            chronyc.chmod(0o755)
            result = subprocess.run(
                [str(COMMAND)],
                env=dict(os.environ, PATH=f"{directory}:{os.environ['PATH']}"),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Cannot talk to daemon", result.stdout)

    def test_help_does_not_require_services(self):
        result = subprocess.run(
            [str(COMMAND), "--help"], capture_output=True, text=True, check=True
        )
        self.assertIn("Usage:", result.stdout)
