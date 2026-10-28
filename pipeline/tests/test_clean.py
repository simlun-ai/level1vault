import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "clean", Path(__file__).resolve().parents[2] / "pipeline/clean.py"
)
cleaner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleaner)


class CleanupTests(unittest.TestCase):
    def test_refuses_mount_container_and_loop(self):
        build = Path("/build-test")
        cases = [
            (
                "1 2 0:1 / /build-test/chroot/dev rw - tmpfs tmpfs rw",
                [],
                {"loopdevices": []},
            ),
            (
                "",
                [{"Id": "running", "Mounts": [{"Source": "/build-test"}]}],
                {"loopdevices": []},
            ),
            ("", [{"Mounts": [{"Source": "/"}]}], {"loopdevices": []}),
            (
                "",
                [],
                {
                    "loopdevices": [
                        {"name": "/dev/loop0", "back-file": "/build-test/out/usb-a.img"}
                    ]
                },
            ),
        ]
        for state in cases:
            with self.subTest(state=state), self.assertRaises(ValueError):
                cleaner.check_idle(build, *state)
        cleaner.check_idle(
            build,
            "",
            [],
            {"loopdevices": [{"name": "/dev/loop1", "back-file": "/unrelated/file"}]},
        )

    def test_host_checks_preserve_all_build_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp).resolve()
            image = build / "usb-a.img"
            image.write_text("valuable workspace")
            with (
                patch.object(
                    cleaner, "output", side_effect=["", json.dumps({"loopdevices": []})]
                ),
                patch.object(cleaner, "mountinfo", return_value=""),
            ):
                cleaner.check_checkout_idle(build)
            self.assertEqual(image.read_text(), "valuable workspace")
            with patch.object(
                cleaner, "output", side_effect=OSError("Docker unavailable")
            ):
                with self.assertRaises(OSError):
                    cleaner.check_checkout_idle(build)
            self.assertEqual(image.read_text(), "valuable workspace")
