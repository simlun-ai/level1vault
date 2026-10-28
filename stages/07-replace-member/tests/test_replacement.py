"""Verify preparation contracts and refusal paths without opening real devices."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "common"))
import member_recovery as recovery  # noqa: E402 - resolve shared helpers after configuring their path


def load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "stages/07-replace-member" / filename
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load("prepare_replacement", "prepare.py")
writer = load("write_replacement", "write.py")
IDS = (
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = self.root / "original"
        self.original.mkdir()
        (self.original / "READY").touch()
        (self.original / "WORKSPACE-INFO.txt").write_text(
            "pair=1\npool=level1-workspace-1\npool_guid=100\n"
        )
        self.old = dict(
            format=1,
            pair="1",
            pool_guid="100",
            members=dict(
                a=dict(zfs_partuuid=IDS[0], esp_uuid="1111-AAAA", minimum_bytes=4096),
                b=dict(zfs_partuuid=IDS[1], esp_uuid="2222-BBBB", minimum_bytes=4096),
            ),
        )
        self.records = self.root / "records"
        self.current = self.records / "pairs/1/current.json"
        self.current.parent.mkdir(parents=True)
        self.current.write_text(json.dumps(self.old))
        self.env = patch.dict(prepare.os.environ, RECORDS_DIR=str(self.records))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.new = dict(
            format=1, images=dict(a=dict(zfs_partuuid=IDS[2], esp_uuid="ABCD-1234"))
        )
        self.destination = self.root / "recovery"

    def build(self, *args, **kwargs):
        out = self.destination / "pairs/1"
        out.mkdir(parents=True)
        (out / "IMAGES-PREPARED").touch()
        (out / "images.json").write_text(json.dumps(self.new))

    def test_prepares_without_initializing_and_embeds_matching_record(self):
        tables = [dict(partitions=[dict(start=2048), dict(size=8)])]

        def embedded(*args):
            return (self.destination / "pairs/1/member-recovery.json").read_text()

        with (
            patch.object(prepare, "partition_table", side_effect=tables),
            patch.object(prepare.subprocess, "run", side_effect=self.build) as build,
            patch.object(prepare, "command") as command,
            patch.object(prepare, "output", side_effect=embedded),
        ):
            prepare.prepare(self.current, self.destination, "1", "a")
        record = json.loads(
            (self.destination / "pairs/1/member-recovery.json").read_text()
        )
        self.assertEqual(record["survivor_partuuid"], IDS[1])
        self.assertEqual(record["minimum_bytes"], 4096)
        self.assertTrue((self.destination / "pairs/1/REPLACEMENT-READY").is_file())
        self.assertEqual(build.call_args.args[0][2], "build")
        self.assertEqual(command.call_args.args[0], "mcopy")
        self.assertEqual(json.loads(self.current.read_text()), self.old)
        self.assertEqual(
            len(list((self.current.parent / "replacements").glob("*.json"))), 1
        )

    def test_invalid_original_or_existing_destination_prevents_build(self):
        for pair, destination in (("2", self.destination), ("1", self.original)):
            with patch.object(prepare.subprocess, "run") as build:
                with self.assertRaises(ValueError):
                    prepare.prepare(self.current, destination, pair, "a")
                build.assert_not_called()

    def test_undersized_partition_does_not_publish_readiness(self):
        with (
            patch.object(
                prepare,
                "partition_table",
                return_value=dict(partitions=[dict(start=2048), dict(size=4)]),
            ),
            patch.object(prepare.subprocess, "run", side_effect=self.build),
        ):
            with self.assertRaisesRegex(ValueError, "too small"):
                prepare.prepare(self.current, self.destination, "1", "a")
        self.assertFalse((self.destination / "pairs/1/REPLACEMENT-READY").exists())


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name).resolve()
        (self.out / "REPLACEMENT-READY").touch()
        (self.out / "usb-a.img").write_bytes(b"empty-image")
        self.record = dict(
            format=1,
            pair="1",
            member="a",
            pool_guid="100",
            minimum_bytes=4096,
            failed_partuuid=IDS[0],
            survivor_partuuid=IDS[1],
            new_partuuid=IDS[2],
            esp_uuid="ABCD-1234",
        )
        (self.out / "member-recovery.json").write_text(json.dumps(self.record))
        self.disk = dict(
            name="/dev/sda",
            device_id=1,
            type="disk",
            tran="usb",
            size=100000,
            children=[dict(name="/dev/sda2", type="part")],
        )
        self.signatures = []
        self.identity_patch = patch.object(writer, "connected_identities")
        self.identity_patch.start()
        self.addCleanup(self.identity_patch.stop)

    def output(self, *args):
        if args[0] == "losetup":
            return json.dumps(dict(loopdevices=[]))
        if args[0] == "blockdev":
            return "512"
        if args[0] == "wipefs":
            return json.dumps(dict(signatures=self.signatures))
        raise AssertionError(args)

    def test_nonblank_or_unsafe_destination_never_writes(self):
        for change, signatures in (
            ({}, [dict(type="zfs_member")]),
            ({}, [dict(type="vfat")]),
            ({"tran": "sata"}, []),
            ({"size": 1}, []),
            ({"mountpoints": ["/"]}, []),
            ({"ro": True}, []),
        ):
            with self.subTest(change=change, signatures=signatures):
                self.signatures = signatures
                with (
                    patch.object(
                        writer, "describe_disk", return_value=dict(self.disk, **change)
                    ),
                    patch.object(writer, "output", side_effect=self.output),
                    patch.object(writer, "imported_devices", return_value=set()),
                    patch.object(writer, "write_image") as write,
                ):
                    with self.assertRaises(ValueError):
                        writer.write(self.out, "1", "a", Path("/dev/sda"))
                write.assert_not_called()

    def test_source_pair_member_or_initialized_marker_refuses_device_access(self):
        for pair, member in (("2", "a"), ("1", "b")):
            with patch.object(writer, "describe_disk") as disk:
                with self.assertRaises(ValueError):
                    writer.write(self.out, pair, member, Path("/dev/sda"))
                disk.assert_not_called()
        (self.out / "READY").touch()
        with self.assertRaises(ValueError):
            writer.write(self.out, "1", "a", Path("/dev/sda"))

    def test_readback_failure_prevents_gpt_changes_and_success_report(self):
        with (
            patch.object(writer, "describe_disk", return_value=self.disk),
            patch.object(writer, "output", side_effect=self.output),
            patch.object(writer, "imported_devices", return_value=set()),
            patch.object(
                writer, "write_image", side_effect=ValueError("readback mismatch")
            ),
            patch.object(writer, "command") as command,
        ):
            with self.assertRaisesRegex(ValueError, "readback"):
                writer.write(self.out, "1", "a", Path("/dev/sda"))
        command.assert_not_called()
        self.assertFalse((self.out / "write-report.json").exists())

    def test_success_writes_only_selected_disk_and_records_verified_completion(self):
        events = []
        with (
            patch.object(writer, "describe_disk", return_value=self.disk),
            patch.object(writer, "output", side_effect=self.output),
            patch.object(writer, "imported_devices", return_value=set()),
            patch.object(
                writer,
                "write_image",
                side_effect=lambda *args: events.append("readback"),
            ),
            patch.object(
                writer, "command", side_effect=lambda *args: events.append(args[0])
            ),
            patch.object(
                writer,
                "inspect_replacement",
                return_value=(self.disk, dict(partitions=[dict(node="/dev/sda1")])),
            ),
            patch.object(
                writer, "mounted_readonly", return_value=nullcontext(self.out)
            ),
        ):
            writer.write(self.out, "1", "a", Path("/dev/sda"))
        self.assertEqual(
            events, ["readback", "sgdisk", "sfdisk", "partprobe", "udevadm"]
        )
        self.assertEqual(
            json.loads((self.out / "write-report.json").read_text())["status"],
            "readback-verified",
        )

    def test_connected_survivor_prevents_disk_access(self):
        original = Path.exists

        def exists(path):
            return str(path) == "/dev/disk/by-partuuid/" + IDS[1] or original(path)

        with (
            patch.object(Path, "exists", exists),
            patch.object(writer, "output", side_effect=self.output),
            patch.object(writer, "describe_disk") as disk,
        ):
            with self.assertRaisesRegex(ValueError, "Disconnect"):
                writer.write(self.out, "1", "a", Path("/dev/sda"))
        disk.assert_not_called()

    def test_missing_make_arguments_fail_before_sudo(self):
        for target in ("prepare-replacement", "write-replacement"):
            result = subprocess.run(
                ["make", target, "MEMBER=", "RECOVERY_DIR=", "DISK=", "SUDO=false"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Set PAIR", result.stderr)


class PartitionTests(unittest.TestCase):
    def test_duplicate_partuuid_and_fat_copies_are_rejected(self):
        record = dict(
            failed_partuuid=IDS[0],
            survivor_partuuid=IDS[1],
            new_partuuid=IDS[2],
            esp_uuid="ABCD-1234",
        )
        good = [
            dict(
                name="/dev/a", children=[dict(partuuid=IDS[2]), dict(uuid="ABCD-1234")]
            ),
            dict(name="/dev/b", children=[dict(partuuid=IDS[1])]),
        ]
        with patch.object(
            recovery, "output", return_value=json.dumps(dict(blockdevices=good))
        ):
            recovery.connected_identities(record, True)
        for extra in (
            dict(partuuid=IDS[1]),
            dict(partuuid=IDS[0]),
            dict(uuid="ABCD-1234"),
        ):
            with patch.object(
                recovery,
                "output",
                return_value=json.dumps(dict(blockdevices=good + [extra])),
            ):
                with self.assertRaisesRegex(ValueError, "copies"):
                    recovery.connected_identities(record, True)

    def test_invalid_layout_and_signatures_are_rejected(self):
        for table in (
            dict(label="dos", partitions=[]),
            dict(label="gpt", partitions=[]),
        ):
            with patch.object(
                recovery, "output", return_value=json.dumps(dict(partitiontable=table))
            ):
                with self.assertRaises(ValueError):
                    recovery.partition_table("/dev/example")
        with patch.object(
            recovery,
            "output",
            return_value=json.dumps(dict(signatures=[dict(type="zfs_member")])),
        ):
            with self.assertRaisesRegex(ValueError, "empty"):
                recovery.empty_partition("/dev/example2")
