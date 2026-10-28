"""Pipeline checks without ZFS IO; actual replication is tested by CI on real pools."""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
