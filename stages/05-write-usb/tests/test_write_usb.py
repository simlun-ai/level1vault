"""Verify disk-writing guards and command ordering without opening real devices."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _typeshed import ReadableBuffer

import copy
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "write_usb", ROOT / "stages/05-write-usb/write.py"
)
assert spec is not None and spec.loader is not None
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)


class WriteUsbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name).resolve()
        (self.out / "READY").touch()
        (self.out / "WORKSPACE-INFO.txt").write_text("pair=4\nstate=replica\n")
        for name in ("usb-a.img", "usb-b.img", "images.json"):
            (self.out / name).write_text(name)
        self.disks: list[dict[str, Any]] = [
            dict(
                name="/dev/sda",
                device_id=1,
                type="disk",
                size=1000,
                tran="usb",
                ro=False,
                children=[
                    dict(name="/dev/sda2", type="part", mountpoints=[None], ro=False)
                ],
            ),
            dict(
                name="/dev/sdb",
                device_id=2,
                type="disk",
                size=1000,
                tran="usb",
                ro=False,
            ),
        ]

    def output(self, *args: str | Path):
        if args[0] == "losetup":
            return json.dumps({"loopdevices": []})
        if args[1] == "list":
            return ""
        raise AssertionError(args)

    def write(self):
        writer.write_pair(self.out, "4", Path("/dev/sda"), Path("/dev/sdb"))

    def test_writes_and_verifies_before_relocating_and_validating_gpt(self):
        events: list[object] = []

        def record_image_write(image: Path, disk: Path):
            return events.append(("write", disk.name))

        def record_command(cmd: list[str], **kw: object):
            return events.append(tuple(cmd))

        with (
            patch.object(writer, "output", side_effect=self.output),
            patch.object(writer, "disk_info", side_effect=self.disks),
            patch.object(
                writer,
                "write_image",
                side_effect=record_image_write,
            ),
            patch.object(
                writer.subprocess,
                "run",
                side_effect=record_command,
            ),
        ):
            self.write()
        self.assertEqual(
            events,
            [
                ("write", "sda"),
                ("sgdisk", "-e", "/dev/sda"),
                ("sfdisk", "--verify", "/dev/sda"),
                ("write", "sdb"),
                ("sgdisk", "-e", "/dev/sdb"),
                ("sfdisk", "--verify", "/dev/sdb"),
                ("sync",),
                ("partprobe", "/dev/sda", "/dev/sdb"),
            ],
        )

    def test_rejects_second_disk_before_writing_first(self):
        self.disks[1]["size"] = 1
        with (
            patch.object(writer, "output", side_effect=self.output),
            patch.object(writer, "disk_info", side_effect=self.disks),
            patch.object(writer.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "too small"):
                self.write()
        run.assert_not_called()

    def test_rejects_same_disk_and_aliases(self):
        with (
            patch.object(writer, "output", side_effect=self.output),
            patch.object(
                writer, "disk_info", side_effect=[self.disks[0], self.disks[0]]
            ),
            patch.object(writer.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "different disks"):
                self.write()
        run.assert_not_called()

    def test_rejects_unsafe_destinations(self):
        cases = [
            ("type", "part"),
            ("tran", "sata"),
            ("ro", True),
            ("mountpoints", ["/"]),
            ("mountpoints", ["[SWAP]"]),
            ("children", [dict(name="/dev/dm-0", type="crypt")]),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                info = copy.deepcopy(self.disks[0])
                info[key] = value
                with self.assertRaises(ValueError):
                    writer.validate_disk(info, 100, set())
        with self.assertRaisesRegex(ValueError, "export it first"):
            writer.validate_disk(self.disks[0], 100, {Path("/dev/sda2")})
        with self.assertRaisesRegex(ValueError, "Not a block device"):
            writer.disk_info(self.out / "usb-a.img")

    def test_source_readiness_failure_prevents_device_access(self):
        (self.out / "READY").unlink()
        with (
            patch.object(writer, "output") as output,
            patch.object(writer.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "READY"):
                self.write()
        output.assert_not_called()
        run.assert_not_called()
        (self.out / "READY").touch()
        with self.assertRaisesRegex(ValueError, "PAIR"):
            writer.verify_images(self.out, "3")

    def test_attached_source_and_imported_destination_are_refused(self):
        loop = json.dumps({"loopdevices": [{"back-file": str(self.out / "usb-a.img")}]})
        with (
            patch.object(writer, "output", return_value=loop),
            patch.object(writer.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "loop mappings"):
                self.write()
        run.assert_not_called()
        with (
            patch.object(
                writer,
                "output",
                side_effect=[
                    json.dumps({"loopdevices": []}),
                    "existing-pool",
                    "  /dev/sda2 ONLINE 0 0 0",
                ],
            ),
            patch.object(writer, "disk_info", side_effect=self.disks),
            patch.object(writer.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "export it first"):
                self.write()
        run.assert_not_called()

    def test_write_failure_stops_before_second_disk(self):
        with (
            patch.object(writer, "output", side_effect=self.output),
            patch.object(writer, "disk_info", side_effect=self.disks),
            patch.object(
                writer, "write_image", side_effect=ValueError("readback mismatch")
            ) as write,
            patch.object(writer.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "readback mismatch"):
                self.write()
        self.assertEqual(write.call_count, 1)
        run.assert_not_called()

    def test_stream_write_fsync_flush_and_readback_exact_extent(self):
        image = self.out / "usb-a.img"
        image.write_bytes(b"payload" * (4 * 1024 * 1024 // 7 + 1))
        disk = self.out / "disk"
        disk.write_bytes(b"x" * (image.stat().st_size + 3000))
        events: list[object] = []
        fsync = writer.os.fsync

        def flush(cmd: list[str], **kwargs: object):
            events.append("flush")
            self.assertEqual(cmd, ["blockdev", "--flushbufs", str(disk)])

        def synced(fd: int):
            events.append("fsync")
            fsync(fd)

        with (
            patch.object(writer.os, "fsync", side_effect=synced),
            patch.object(writer.subprocess, "run", side_effect=flush),
        ):
            writer.write_image(image, disk)
        self.assertEqual(events, ["fsync", "flush"])
        self.assertEqual(disk.read_bytes(), image.read_bytes() + b"x" * 3000)

    def test_readback_rejects_corruption_short_reads_and_flush_errors(self):
        disk = self.out / "disk"
        expected = hashlib.sha256(b"expected").hexdigest()
        for data, message in (
            (b"corrupt!", "checksum mismatch"),
            (b"short", "Short USB readback"),
        ):
            disk.write_bytes(data)
            with patch.object(writer.subprocess, "run"):
                with self.assertRaisesRegex(ValueError, message):
                    writer.readback(disk, 8, expected)
        with patch.object(
            writer.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, "blockdev"),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                writer.readback(disk, 8, expected)

    def test_readback_propagates_device_read_errors(self):
        with (
            patch.object(writer.subprocess, "run"),
            patch.object(Path, "open", side_effect=OSError("device read error")),
        ):
            with self.assertRaisesRegex(OSError, "device read error"):
                writer.readback(self.out / "disk", 8, "0" * 64)

    def test_fsync_error_prevents_readback(self):
        image = self.out / "usb-a.img"
        disk = self.out / "disk"
        disk.write_bytes(b"x" * 100)
        with (
            patch.object(writer.os, "fsync", side_effect=OSError("flush failed")),
            patch.object(sys.modules["usb_io"], "readback") as readback,
        ):
            with self.assertRaisesRegex(OSError, "flush failed"):
                writer.write_image(image, disk)
        readback.assert_not_called()

    def test_zero_byte_write_stops_instead_of_looping(self):
        original = Path.open

        def opened(
            path: Path,
            mode: str = "r",
            buffering: int = -1,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ):
            if path.name == "disk":
                from unittest.mock import MagicMock

                target = MagicMock()
                target.__enter__.return_value.write.return_value = 0
                return target
            return original(path, mode, buffering, encoding, errors, newline)

        with (
            patch.object(Path, "open", opened),
            patch.object(sys.modules["usb_io"], "readback") as readback,
        ):
            with self.assertRaisesRegex(ValueError, "Short USB write"):
                writer.write_image(self.out / "usb-a.img", self.out / "disk")
        readback.assert_not_called()

    def test_stream_writer_handles_partial_writes(self):
        image = self.out / "usb-a.img"
        data = image.read_bytes()

        class PartialDisk(io.BytesIO):
            def write(self, block: ReadableBuffer):
                return super().write(memoryview(block)[:2])

            def fileno(self):
                return 123

            def close(self):
                pass

        target = PartialDisk()
        original = Path.open

        def opened(
            path: Path,
            mode: str = "r",
            buffering: int = -1,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ):
            return (
                target
                if path.name == "disk"
                else original(path, mode, buffering, encoding, errors, newline)
            )

        with (
            patch.object(Path, "open", opened),
            patch.object(writer.os, "fsync"),
            patch.object(sys.modules["usb_io"], "readback") as readback,
        ):
            writer.write_image(image, self.out / "disk")
        self.assertEqual(target.getvalue(), data)
        readback.assert_called_once_with(
            self.out / "disk", len(data), hashlib.sha256(data).hexdigest()
        )

    def test_make_requires_both_arguments_before_sudo(self):
        for disks in ([], ["DISK_A=/dev/sda"], ["DISK_B=/dev/sdb"]):
            result = subprocess.run(
                ["make", "write-usb", "SUDO=false", "DISK_A=", "DISK_B=", *disks],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Set both DISK_A and DISK_B", result.stderr)
            self.assertNotIn("python3 stages/05-write-usb/write.py", result.stdout)
