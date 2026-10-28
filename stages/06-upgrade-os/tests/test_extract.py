"""Check the shared extraction contract used by assembly and OS release builds."""

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "shared_live_payload", ROOT / "common/live_payload.py"
)
payload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(payload)


class ExtractTests(unittest.TestCase):
    def test_selects_matching_kernel_and_exports_flat_regular_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            source = base / "iso"
            (source / "live").mkdir(parents=True)
            (source / ".disk").mkdir()
            for name in (
                "filesystem.squashfs",
                "vmlinuz-6.12-fixture",
                "initrd.img-6.12-fixture",
            ):
                (source / "live" / name).write_text(name)
            (source / ".disk/live-uuid").write_text("boot uuid")
            (source / ".disk/live-uuid").chmod(0o444)
            (source / ".disk").chmod(0o555)

            def extract_command(args, **kwargs):
                shutil.copytree(source, Path(args[-1]))

            metadata = {
                "kernel-version": "6.12-fixture",
                "zfsutils-version": "2.3-fixture",
                "source-commit": "1" * 40,
                "packages.tsv": "zfsutils-linux\t2.3-fixture",
            }
            with (
                patch.object(payload.subprocess, "run", side_effect=extract_command),
                patch.object(
                    payload, "metadata", side_effect=lambda squash, name: metadata[name]
                ),
            ):
                release = payload.extract(base / "input.iso", base / "release")
            self.assertEqual(
                (base / "release/live/vmlinuz").read_text(), "vmlinuz-6.12-fixture"
            )
            self.assertEqual(
                (base / "release/live/initrd.img").read_text(),
                "initrd.img-6.12-fixture",
            )
            self.assertEqual(release["kernel"], "6.12-fixture")
            self.assertIn(".disk/live-uuid", release["files"])
            self.assertTrue((base / "release/live/release.json").is_file())
            # Local export must be able to populate directories inherited from a read-only ISO.
            self.assertEqual((base / "release/.disk").stat().st_mode & 0o777, 0o755)
            self.assertEqual(
                (base / "release/.disk/live-uuid").stat().st_mode & 0o777, 0o644
            )
            self.assertEqual(
                (base / "release/.disk/live-uuid").read_text(), "boot uuid"
            )
