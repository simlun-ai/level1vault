"""Exercise member recovery with mocked devices and realistic ZFS status output."""

import importlib.machinery
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "common"))
import member_recovery as recovery  # noqa: E402 - resolve shared helpers after configuring their path

loader = importlib.machinery.SourceFileLoader(
    "workspace_replace", str(ROOT / "runtime/workspace-replace")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
replace = importlib.util.module_from_spec(spec)
loader.exec_module(replace)

RECORD = dict(
    format=1,
    pair="1",
    member="a",
    pool_guid="100",
    minimum_bytes=4096,
    failed_partuuid="11111111-1111-4111-8111-111111111111",
    survivor_partuuid="22222222-2222-4222-8222-222222222222",
    new_partuuid="33333333-3333-4333-8333-333333333333",
    esp_uuid="ABCD-1234",
)


def status(state="UNAVAIL", a="11", errors="No known data errors", counters="0 0 0"):
    return f"""  pool: level1-workspace-1
 state: DEGRADED
config:

        NAME                     STATE     READ WRITE CKSUM
        level1-workspace-1       DEGRADED     0     0     0
          50                     DEGRADED     0     0     0
            {a}                  {state}     {counters}
            12                   ONLINE       0     0     0

errors: {errors}
"""


def label(leaf="12", pool="100"):
    return f"LABEL 0\n name: 'level1-workspace-1'\n pool_guid: {pool}\n guid: {leaf}\n vdev_tree:\n type: 'mirror'\n"


class ReplaceTests(unittest.TestCase):
    def test_status_requires_exactly_one_two_member_mirror(self):
        self.assertEqual(replace.mirror_leaves(status())[0][1:3], ("11", "UNAVAIL"))
        self.assertEqual(
            len(
                replace.mirror_leaves(
                    status().replace(
                        "UNAVAIL     0 0 0", "UNAVAIL     0 0 0  was /dev/old"
                    )
                )
            ),
            2,
        )
        for text in (
            status().replace("          50", "        50"),
            status().replace("\nerrors:", "\n            13 ONLINE 0 0 0\nerrors:"),
            status().replace("            12", "              12"),
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                replace.mirror_leaves(text)

    def test_wait_is_not_proof_of_success(self):
        for text in (
            status(),
            status("ONLINE", a="13", counters="0 1 0"),
            status("ONLINE", a="13", errors="permanent errors"),
            status("ONLINE", a="14"),
        ):
            with (
                self.subTest(text=text),
                patch.object(replace, "output", return_value=text),
            ):
                with self.assertRaises(ValueError):
                    replace.check_complete("level1-workspace-1", "12", "13")
        with patch.object(
            replace, "output", side_effect=[status("ONLINE", a="13"), "ONLINE"]
        ):
            replace.check_complete("level1-workspace-1", "12", "13")

    def query(self, *args):
        if args[0] == "lsblk":
            return "/dev/sda"
        if args[0] == "zdb":
            return label(
                "13" if str(args[-1]).endswith(RECORD["new_partuuid"]) else "12",
                self.pool_guid,
            )
        if args[0] == "blockdev":
            return "4096"
        if args[:2] == ("zpool", "list"):
            return "ONLINE" if "health" in args else "level1-workspace-1"
        if args[:2] == ("zpool", "get"):
            return "100"
        if args[:2] == ("zpool", "status"):
            self.status_reads += 1
            return (
                status("ONLINE", a="13")
                if self.status_reads > 1 or self.resuming
                else status()
            )
        if args[0] == "zfs":
            return {
                "org.level1vault:pair": "1",
                "org.level1vault:state": "active",
                "readonly": "off",
                "encryption": "aes-256-gcm",
                "encryptionroot": "level1-workspace-1/data",
                "keyformat": "passphrase",
            }[args[-2]]
        raise AssertionError(args)

    def recover(self, resuming=False, wrong_pool=False):
        self.status_reads = 0
        self.pool_guid = "999" if wrong_pool else "100"
        self.resuming = resuming
        info = dict(model="USB", serial="new")
        table = dict(
            partitions=[
                dict(node="/dev/sda1"),
                dict(node="/dev/disk/by-partuuid/" + RECORD["new_partuuid"]),
            ]
        )
        with (
            patch.object(Path, "read_text", return_value=json.dumps(RECORD)),
            patch.object(
                Path,
                "exists",
                lambda p: (
                    str(p) != "/dev/disk/by-partuuid/" + RECORD["failed_partuuid"]
                ),
            ),
            patch.object(replace, "output", side_effect=self.query),
            patch.object(replace, "connected_identities"),
            patch.object(replace, "inspect_replacement", return_value=(info, table)),
            patch.object(replace, "empty_partition") as empty,
            patch.object(replace, "command") as command,
        ):
            if wrong_pool:
                with self.assertRaisesRegex(ValueError, "different pool"):
                    replace.replace("1", True)
                command.assert_not_called()
            else:
                replace.replace("1", True)
                commands = [call.args for call in command.call_args_list]
                replacements = [c for c in commands if c[:2] == ("zpool", "replace")]
                self.assertEqual(len(replacements), 0 if resuming else 1)
                self.assertIn(
                    ("zpool", "wait", "-t", "resilver,replace", "level1-workspace-1"),
                    commands,
                )
                self.assertFalse(any("-f" in c for c in commands))
                self.assertEqual(empty.call_count, 0 if resuming else 1)

    def test_replaces_missing_leaf_and_verifies_final_membership(self):
        self.recover()

    def test_can_wait_again_after_successful_replace(self):
        self.recover(resuming=True)

    def test_wrong_survivor_pool_never_replaces(self):
        self.recover(wrong_pool=True)

    def test_fencing_is_required_before_reading_devices(self):
        with patch.object(replace, "output") as query:
            with self.assertRaisesRegex(ValueError, "fenced"):
                replace.replace("1", False)
        query.assert_not_called()

    def test_record_rejects_reused_identifiers_and_bad_sizes(self):
        for change in (
            {"new_partuuid": RECORD["survivor_partuuid"]},
            {"minimum_bytes": 0},
            {"pair": "01"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                recovery.validate_record(dict(RECORD, **change))

    def test_boot_exception_never_allows_a_writable_or_unrelated_medium(self):
        info = dict(
            name="/dev/sda",
            type="disk",
            tran="usb",
            device_id=1,
            children=[
                dict(name="/dev/sda1", type="part", mountpoints=["/run/live/medium"])
            ],
        )
        table = dict(partitions=[dict(node="/dev/sda1"), dict(node="/dev/sda2")])
        for source, options in (
            ("/dev/sda1", "rw,relatime"),
            ("/dev/sdb1", "ro,relatime"),
        ):
            with (
                patch.object(recovery, "describe_disk", return_value=info),
                patch.object(recovery, "partition_table", return_value=table),
                patch.object(recovery, "output", side_effect=[source, options]),
            ):
                with self.assertRaisesRegex(ValueError, "read-only"):
                    recovery.inspect_replacement(Path("/dev/sda"), RECORD, boot=True)
