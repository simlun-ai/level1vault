"""Pipeline checks without ZFS IO; actual replication is tested by CI on real pools."""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

loader = importlib.machinery.SourceFileLoader(
    "workspace_sync",
    str(Path(__file__).resolve().parents[2] / "runtime/workspace-sync"),
)
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None and spec.loader is not None
sync = importlib.util.module_from_spec(spec)
loader.exec_module(sync)


class TransferTests(unittest.TestCase):
    def test_both_pipeline_statuses_required_and_receive_never_forces(self):
        for send_rc, receive_rc in [(0, 0), (1, 0), (0, 1), (1, 1)]:
            with self.subTest(send=send_rc, receive=receive_rc):
                sender, receiver = MagicMock(), MagicMock()
                sender.wait.return_value = send_rc
                receiver.wait.return_value = receive_rc
                with patch.object(
                    sync.subprocess, "Popen", side_effect=[sender, receiver]
                ) as spawn:
                    if send_rc or receive_rc:
                        with self.assertRaisesRegex(RuntimeError, "Transfer failed"):
                            sync.transfer(
                                ["zfs", "send", "-w", "level1-workspace-1/data@snap"],
                                "level1-workspace-2/data",
                                "on",
                            )
                    else:
                        sync.transfer(
                            ["zfs", "send", "-w", "level1-workspace-1/data@snap"],
                            "level1-workspace-2/data",
                            "on",
                        )
                    args = spawn.call_args_list[1].args[0]
                    self.assertIn("-s", args)
                    self.assertIn("-u", args)
                    self.assertIn("readonly=on", args)
                    self.assertNotIn("-F", args)
                    sender.wait.assert_called_once()
                    receiver.wait.assert_called_once()

    def test_interruption_stops_and_reaps_both_children(self):
        sender, receiver = MagicMock(), MagicMock()
        sender.poll.return_value = receiver.poll.return_value = None
        receiver.wait.side_effect = [KeyboardInterrupt, 1]
        with patch.object(sync.subprocess, "Popen", side_effect=[sender, receiver]):
            with self.assertRaises(KeyboardInterrupt):
                sync.transfer(
                    ["zfs", "send", "-w", "level1-workspace-1/data@snap"],
                    "level1-workspace-2/data",
                    "on",
                )
        sender.terminate.assert_called_once()
        receiver.terminate.assert_called_once()
        sender.wait.assert_called_once()
        self.assertEqual(receiver.wait.call_count, 2)

    def test_failed_receiver_start_closes_pipe_and_reaps_sender(self):
        sender = MagicMock()
        sender.poll.return_value = None
        with patch.object(
            sync.subprocess, "Popen", side_effect=[sender, OSError("spawn failed")]
        ):
            with self.assertRaises(OSError):
                sync.transfer(
                    ["zfs", "send", "-w", "level1-workspace-1/data@snap"],
                    "level1-workspace-2/data",
                    "on",
                )
        sender.stdout.close.assert_called_once()
        sender.terminate.assert_called_once()
        sender.wait.assert_called_once()


class ImportTests(unittest.TestCase):
    def test_import_requires_explicit_force_and_still_checks_identity(self):
        for force in (False, True):
            with (
                self.subTest(force=force),
                patch.object(sync, "run", return_value=""),
                patch.object(sync.subprocess, "check_output", return_value="") as imp,
                patch.object(sync, "prop", return_value="2"),
            ):
                self.assertEqual(sync.import_pair("2", force), "level1-workspace-2")
                args = imp.call_args.args[0]
                self.assertEqual("-f" in args, force)
                self.assertIn("-N", args)
                self.assertEqual(args[-1], "level1-workspace-2")
                self.assertEqual(imp.call_args.kwargs["env"]["LC_ALL"], "C")
            with (
                patch.object(sync, "run", return_value=""),
                patch.object(sync.subprocess, "check_output", return_value=""),
                patch.object(sync, "prop", return_value="3"),
                self.assertRaisesRegex(RuntimeError, "identity"),
            ):
                sync.import_pair("2", force)

    def test_already_imported_pair_is_not_reimported(self):
        with (
            patch.object(sync, "run", return_value="level1-workspace-2"),
            patch.object(sync.subprocess, "check_output") as imp,
            patch.object(sync, "prop", return_value="2"),
        ):
            sync.import_pair("2", True)
            imp.assert_not_called()

    def test_foreign_owner_failure_explains_fencing_without_retrying(self):
        for detail in (
            "pool was previously in use from another system",
            "pool may be in use from other system",
        ):
            with (
                self.subTest(detail=detail),
                patch.object(sync, "run", return_value=""),
                patch.object(
                    sync.subprocess,
                    "check_output",
                    side_effect=sync.subprocess.CalledProcessError(
                        1, "zpool", output=detail
                    ),
                ) as imp,
                patch.object(sync, "prop") as prop,
                self.assertRaisesRegex(
                    RuntimeError, "neither mirror member.*--force-import"
                ),
            ):
                sync.import_pair("2")
            imp.assert_called_once()
            prop.assert_not_called()

    def test_other_import_failures_preserve_diagnostic_without_force_hint(self):
        with (
            patch.object(sync, "run", return_value=""),
            patch.object(
                sync.subprocess,
                "check_output",
                side_effect=sync.subprocess.CalledProcessError(
                    1, "zpool", output="no such pool"
                ),
            ) as imp,
            self.assertRaisesRegex(RuntimeError, "no such pool") as error,
        ):
            sync.import_pair("2")
        self.assertNotIn("--force-import", str(error.exception))
        imp.assert_called_once()

    def test_force_import_does_not_bypass_destination_health(self):
        with (
            patch.object(
                sync,
                "import_pair",
                side_effect=["level1-workspace-1", "level1-workspace-2"],
            ) as imp,
            patch.object(sync, "run", side_effect=["guid1", "guid2", "DEGRADED"]),
            patch.object(sync, "transfer") as transfer,
            self.assertRaisesRegex(RuntimeError, "both mirror members"),
        ):
            sync.synchronize("1", "2", force_import=True)
        self.assertEqual(imp.call_args_list[0].kwargs, {"force_import": True})
        self.assertEqual(imp.call_args_list[1].kwargs, {"force_import": True})
        transfer.assert_not_called()

    def test_cli_forwards_force_import_to_source_targets_and_pending_retry(self):
        for force in (False, True):
            argv = (
                ["workspace-sync"]
                + (["--force-import"] if force else [])
                + ["1", "2", "3"]
            )
            with (
                self.subTest(force=force),
                patch.object(sync.sys, "argv", argv),
                patch.object(sync.os, "geteuid", return_value=0),
                patch.object(sync, "open", mock_open(), create=True),
                patch.object(sync.fcntl, "flock"),
                patch.object(sync.os.path, "exists", return_value=False),
                patch.object(sync, "command"),
                patch.object(
                    sync, "import_pair", return_value="level1-workspace-1"
                ) as imp,
                patch.object(sync, "validate_data"),
                patch.object(sync, "prop", side_effect=["active", "off"]),
                patch.object(sync, "create_checkpoint", return_value="new"),
                patch.object(
                    sync, "synchronize", side_effect=["pending", "new", "new"]
                ) as transfer,
            ):
                sync.main()
            imp.assert_called_once_with("1", force_import=force)
            self.assertEqual(transfer.call_count, 3)
            self.assertEqual(
                [c.args[1] for c in transfer.call_args_list], ["2", "2", "3"]
            )
            for call in transfer.call_args_list:
                self.assertEqual(call.kwargs, {"force_import": force})


class PairTests(unittest.TestCase):
    def test_pair_ids_are_unbounded_canonical_positive_numbers(self):
        self.assertEqual(sync.pool_name("12345"), "level1-workspace-12345")
        for bad in ["0", "01", "-1", "backup1", "1/data", " 2", "2.0"]:
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                sync.pair_id(bad)

    def test_discovery_sorts_numbers_and_deduplicates_imported_pairs(self):
        with (
            patch.object(
                sync,
                "run",
                return_value="level1-workspace-10\nlevel1-workspace-2\nother",
            ),
            patch.object(
                sync.subprocess,
                "check_output",
                return_value="  pool: level1-workspace-3\n  pool: level1-workspace-2\n  pool: level1-workspace-backup1\n",
            ),
        ):
            self.assertEqual(sync.discover_pairs(), ["2", "3", "10"])

    def test_activation_requires_fencing_before_import(self):
        with patch.object(sync, "import_pair") as imp:
            with self.assertRaisesRegex(RuntimeError, "Fence"):
                sync.activate("2")
            imp.assert_not_called()

    def test_activation_refuses_another_observable_active_pair(self):
        with (
            patch.object(sync, "import_pair", return_value="level1-workspace-2"),
            patch.object(
                sync, "run", return_value="level1-workspace-1\nlevel1-workspace-2"
            ),
            patch.object(sync, "prop", return_value="active"),
            patch.object(sync, "command") as command,
        ):
            with self.assertRaisesRegex(RuntimeError, "still active"):
                sync.activate("2", fenced=True)
            command.assert_not_called()
