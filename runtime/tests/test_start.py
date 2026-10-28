"""Exercise the interactive session wrapper without real mounts, users, or shutdown."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class StartTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.log = self.base / "calls"
        marker = self.base / "source-commit"
        marker.touch()
        self.script = self.base / "start"
        self.script.write_text(
            (ROOT / "runtime/start")
            .read_text()
            .replace("/usr/local/share/level1vault/source-commit", str(marker))
        )
        self.env = dict(
            os.environ, PATH=f"{self.base}:{os.environ['PATH']}", LOG=str(self.log)
        )
        self.tool("id", 'echo "${UID_RESULT:-0}"')
        self.tool("workspace-mount", 'exit "${MOUNT_STATUS:-0}"')
        self.tool("workspace-umount", 'exit "${EXPORT_STATUS:-0}"')
        self.tool("sudo", 'exit "${TMUX_STATUS:-0}"')
        self.tool("poweroff", ":")
        self.tool("python3", 'exit "${CHECK_STATUS:-0}"')

    def tool(self, name, body):
        target = self.base / name
        target.write_text(f'#!/bin/sh\nset -eu\necho "{name} $*" >> "$LOG"\n{body}\n')
        target.chmod(0o755)

    def run_start(self, answers="", *args):
        result = subprocess.run(
            ["sh", str(self.script), *args],
            input=answers,
            env=self.env,
            text=True,
            capture_output=True,
        )
        calls = self.log.read_text() if self.log.exists() else ""
        return result, calls

    def test_defaults_mount_then_login_tmux_then_export_then_poweroff(self):
        result, calls = self.run_start("\n\n", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        order = [
            "workspace-mount 2",
            "workspace-home check",
            "sudo -iu simlun tmux new-session -A -s workspace -c /home/simlun",
            "workspace-umount",
            "poweroff",
        ]
        positions = [calls.index(value) for value in order]
        self.assertEqual(positions, sorted(positions))

    def test_declining_export_skips_poweroff_prompt(self):
        result, calls = self.run_start("n\n")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("workspace-umount\n", calls)
        self.assertNotIn("Power off?", result.stdout)

    def test_declining_shutdown_leaves_exported_system_at_root(self):
        result, calls = self.run_start("Y\nn\n")
        self.assertEqual(result.returncode, 0)
        self.assertIn("workspace-umount", calls)
        self.assertNotIn("poweroff", calls)

    def test_eof_does_not_accept_default(self):
        result, calls = self.run_start()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("workspace-umount", calls)
        self.assertNotIn("poweroff", calls)

    def test_failed_export_including_detached_tmux_never_offers_poweroff(self):
        self.env["EXPORT_STATUS"] = "1"
        result, calls = self.run_start("Y\nY\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reattach", result.stdout)
        self.assertNotIn("Power off?", result.stdout)
        self.assertNotIn("poweroff", calls)

    def test_mount_home_check_and_tmux_failures_stop_shutdown_flow(self):
        for variable in ("MOUNT_STATUS", "CHECK_STATUS", "TMUX_STATUS"):
            with self.subTest(variable=variable):
                self.log.unlink(missing_ok=True)
                self.env[variable] = "1"
                result, calls = self.run_start("Y\nY\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("workspace-umount", calls)
                self.assertNotIn("poweroff", calls)
                del self.env[variable]

    def test_nonroot_never_mounts(self):
        self.env["UID_RESULT"] = "1000"
        result, calls = self.run_start()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("workspace-mount", calls)
