"""Host preparation contracts with no Docker, sudo or disk mutations."""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "pipeline" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load("prepare-upgrade")
pair = load("add-pair")


class PreparationTests(unittest.TestCase):
    def test_upgrade_builds_without_usb_access_and_requires_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp) / "2026-10-18"

            def complete(args, **kwargs):
                self.assertEqual(args[2], "build-upgrade")
                self.assertEqual(Path(args[3]), build)
                self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
                build.mkdir()
                (build / "UPGRADE-READY").touch()

            with (
                patch.object(release.subprocess, "check_output", return_value=""),
                patch.object(release.subprocess, "run", side_effect=complete) as run,
            ):
                release.prepare(build)
                run.assert_called_once()
                with self.assertRaisesRegex(ValueError, "new BUILD_DIR"):
                    release.prepare(build)

    def test_dirty_or_failed_build_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp) / "new"
            with (
                patch.object(
                    release.subprocess, "check_output", return_value="?? file"
                ),
                patch.object(release.subprocess, "run") as run,
            ):
                with self.assertRaisesRegex(ValueError, "Commit source"):
                    release.prepare(build)
                run.assert_not_called()
            with (
                patch.object(release.subprocess, "check_output", return_value=""),
                patch.object(
                    release.subprocess,
                    "run",
                    side_effect=subprocess.CalledProcessError(1, "build"),
                ),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    release.prepare(build)
            self.assertFalse((build / "UPGRADE-READY").exists())
            with (
                patch.object(release.subprocess, "check_output", return_value=""),
                patch.object(release.subprocess, "run"),
            ):
                with self.assertRaisesRegex(ValueError, "did not publish"):
                    release.prepare(build)

    def test_add_pair_authenticates_assembles_then_initializes_a_replica(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp)
            (build / "live").mkdir()
            (build / "live/live-image-amd64.hybrid.iso").touch()
            out = build / "pairs/2"
            with (
                patch.dict(os.environ, SUDO="sudo"),
                patch.object(pair.subprocess, "run") as run,
            ):
                pair.prepare(build, out, "2")
            calls = [call.args[0] for call in run.call_args_list]
            self.assertEqual(calls[0], ["sudo", "-v"])
            self.assertEqual(calls[1][2:], ["assemble", str(build), str(out)])
            self.assertEqual(
                calls[2],
                [
                    "sudo",
                    str(ROOT / "stages/03-initialize-pair/initialize.sh"),
                    str(out),
                    "2",
                ],
            )

    def test_add_pair_stops_on_assembly_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp)
            (build / "live").mkdir()
            (build / "live/live-image-amd64.hybrid.iso").touch()
            with (
                patch.dict(os.environ, SUDO=""),
                patch.object(
                    pair.subprocess,
                    "run",
                    side_effect=subprocess.CalledProcessError(1, "assemble"),
                ) as run,
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    pair.prepare(build, build / "pairs/2", "2")
                run.assert_called_once()
            with patch.object(pair.subprocess, "run") as run:
                for out, number in [
                    (build, "2"),
                    (build / "pairs/2", "02"),
                    (build.parent / "outside", "2"),
                ]:
                    with self.assertRaises(ValueError):
                        pair.prepare(build, out, number)
                run.assert_not_called()

    def test_make_requires_explicit_upgrade_release_and_additional_pair(self):
        env = dict(os.environ)
        for name in ("PAIR", "ACTIVE", "UPGRADE_DIR"):
            env.pop(name, None)
        for args in [
            ("upgrade-os", "PAIR=2", "DISK_A=/a", "DISK_B=/b"),
            ("add-pair",),
            ("add-pair", "PAIR=2", "ACTIVE=1"),
        ]:
            result = subprocess.run(
                ["make", *args, "SUDO=false"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("pipeline/lifecycle.py", result.stdout)
