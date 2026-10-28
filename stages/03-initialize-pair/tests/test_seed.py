"""Check pre-flash seeding and cleanup without opening disks or loading ZFS."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "seed.py"
spec = importlib.util.spec_from_file_location("seed_pairs", SCRIPT)
assert spec is not None and spec.loader is not None
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)


class SeedTests(unittest.TestCase):
    def test_seed_imports_by_guid_replicates_and_publishes_readiness_after_cleanup(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp).resolve()
            for pair in (1, 2):
                out = build / "pairs" / str(pair)
                out.mkdir(parents=True)
                (out / "READY").touch()
                (out / "WORKSPACE-INFO.txt").write_text(
                    f"pair={pair}\nstate={'active' if pair == 1 else 'replica'}\npool_guid={pair}00\n"
                )
                (out / "images.json").write_text(
                    json.dumps(
                        {
                            "images": {
                                "a": {"zfs_partuuid": "aaa"},
                                "b": {"zfs_partuuid": "bbb"},
                            }
                        }
                    )
                )
                for side in ("a", "b"):
                    (out / f"usb-{side}.img").write_text(side)
            calls = []

            def output(*args):
                if args[:2] == ("losetup", "-j"):
                    return ""
                if args[0] == "losetup":
                    return (
                        "/dev/loop-"
                        + Path(args[-1]).parent.name
                        + Path(args[-1]).stem[-1]
                    )
                if args[0] == "blkid":
                    return "aaa" if str(args[-1]).endswith("ap2") else "bbb"
                raise AssertionError(args)

            def command(*args):
                calls.append(tuple(map(str, args)))
                if str(args[0]).endswith("workspace-sync"):
                    self.assertFalse(
                        any(
                            (build / "pairs" / str(pair) / "READY").exists()
                            for pair in (1, 2)
                        )
                    )

            with (
                patch.object(seed, "output", side_effect=output),
                patch.object(seed, "command", side_effect=command),
                patch.object(
                    seed,
                    "imported_pools",
                    side_effect=[
                        {},
                        {"level1-workspace-1": "100", "level1-workspace-2": "200"},
                    ],
                ),
            ):
                seed.seed(build, 2)
            imports = [args for args in calls if args[:2] == ("zpool", "import")]
            self.assertEqual([args[-1] for args in imports], ["100", "200"])
            self.assertTrue(all("-N" in args and "-R" in args for args in imports))
            self.assertIn((str(seed.ROOT / "runtime/workspace-sync"), "1", "2"), calls)
            self.assertEqual(sum(args[:2] == ("losetup", "-d") for args in calls), 4)
            for pair in (1, 2):
                self.assertTrue((build / "pairs" / str(pair) / "READY").exists())
                self.assertFalse((build / "pairs" / str(pair) / "SHA256SUMS").exists())

    def test_failed_export_retains_its_loops_and_never_touches_unrelated_pool(self):
        with (
            patch.object(
                seed,
                "imported_pools",
                return_value={"level1-workspace-1": "100", "unrelated": "999"},
            ),
            patch.object(
                seed, "command", side_effect=subprocess.CalledProcessError(1, "export")
            ) as command,
        ):
            with self.assertRaisesRegex(RuntimeError, "retained loops"):
                seed.release(
                    [("level1-workspace-1", "100", ["/dev/loop1", "/dev/loop2"])]
                )
        command.assert_called_once_with("zpool", "export", "level1-workspace-1")

    def test_foreign_pool_guid_is_not_exported_and_its_loops_are_retained(self):
        with (
            patch.object(
                seed, "imported_pools", return_value={"level1-workspace-1": "foreign"}
            ),
            patch.object(seed, "command") as command,
        ):
            with self.assertRaisesRegex(RuntimeError, "Unexpected pool GUID"):
                seed.release([("level1-workspace-1", "100", ["/dev/loop1"])])
        command.assert_not_called()
