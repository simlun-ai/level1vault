"""Check batch ordering, fail-fast behavior and secret handling without host mutations."""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "prepare_pairs", ROOT / "pipeline/prepare-pairs.py"
)
assert spec is not None and spec.loader is not None
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


class PreparePairsTests(unittest.TestCase):
    def test_build_once_then_initialize_seed_and_verify_without_late_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp).resolve() / "new"
            calls: list[tuple[list[str], dict[str, object]]] = []

            def command(*args: str | Path, **kwargs: object):
                calls.append((list(map(str, args)), kwargs))
                if args[2] == "build":
                    build.mkdir()

            with patch.object(batch, "command", side_effect=command):
                batch.prepare(build, 3, "fixture-passphrase")
            self.assertEqual(
                [args[2] for args, _ in calls if args[1].endswith("buildkit.sh")],
                ["build", "check", "assemble", "assemble"],
            )
            initializations = [
                (args, options)
                for args, options in calls
                if args[1].endswith("initialize.sh")
            ]
            self.assertEqual(len(initializations), 3)
            self.assertEqual(initializations[0][1]["input"], "fixture-passphrase\n")
            self.assertEqual(initializations[0][0][-1], "--active")
            for args, options in initializations[1:]:
                self.assertNotIn("--active", args)
                self.assertEqual(options["stdin"], subprocess.DEVNULL)
            for args, options in calls:
                self.assertNotIn("fixture-passphrase", " ".join(args))
                self.assertNotIn("fixture-passphrase", str(options.get("env", {})))
            self.assertEqual(
                len([args for args, _ in calls if args[1].endswith("/records.py")]), 3
            )
            self.assertFalse((build / "PREPARING-PAIRS").exists())

    def test_existing_build_is_rejected_without_any_host_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(batch, "command") as command:
                with self.assertRaisesRegex(ValueError, "new BUILD_DIR"):
                    batch.preflight(Path(tmp), "3")
            command.assert_not_called()

    def test_build_failure_stops_before_initialization(self):
        with patch.object(
            batch, "command", side_effect=subprocess.CalledProcessError(1, "build")
        ) as command:
            with self.assertRaises(subprocess.CalledProcessError):
                batch.prepare(Path("/unused"), 3, "fixture-passphrase")
        self.assertEqual(command.call_count, 1)

    def test_passphrase_must_be_confirmed_upfront(self):
        with (
            patch.object(batch.sys.stdin, "isatty", return_value=True),
            patch.object(
                batch.getpass,
                "getpass",
                side_effect=["fixture-passphrase", "different"],
            ),
        ):
            with self.assertRaisesRegex(ValueError, "do not match"):
                batch.ask_passphrase()

    def test_main_collects_secret_before_starting_work(self):
        events: list[object] = []

        def record_preflight(*args: str | Path):
            return events.append("preflight")

        def record_prompt():
            return events.append("prompt") or "fixture-passphrase"

        def record_build(*args: str | Path):
            return events.append("build")

        with (
            patch.object(
                batch.sys,
                "argv",
                ["prepare", "--build-dir", "build/new", "--count", "3"],
            ),
            patch.object(batch, "preflight", side_effect=record_preflight),
            patch.object(
                batch,
                "ask_passphrase",
                side_effect=record_prompt,
            ),
            patch.object(batch, "prepare", side_effect=record_build),
        ):
            batch.main()
        self.assertEqual(events, ["preflight", "prompt", "build"])

    def test_make_requires_pair_count_before_sudo(self):
        for count in ("", "0", "-1", "03", "oops"):
            result = subprocess.run(
                ["make", "prepare-pairs", "N=" + count, "SUDO=false"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Set N", result.stderr)
            self.assertNotIn("python3 pipeline/prepare-pairs.py", result.stdout)
