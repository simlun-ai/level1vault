"""Pipeline checks without ZFS IO; actual replication is tested by CI on real pools."""
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

loader = importlib.machinery.SourceFileLoader('workspace_sync', str(Path(__file__).resolve().parents[1]/'live/workspace-sync'))
spec = importlib.util.spec_from_loader(loader.name, loader)
sync = importlib.util.module_from_spec(spec)
loader.exec_module(sync)


class TransferTests(unittest.TestCase):
    def test_both_pipeline_statuses_required_and_receive_never_forces(self):
        for send_rc, receive_rc in [(0, 0), (1, 0), (0, 1), (1, 1)]:
            with self.subTest(send=send_rc, receive=receive_rc):
                sender, receiver = MagicMock(), MagicMock()
                sender.wait.return_value = send_rc
                receiver.wait.return_value = receive_rc
                with patch.object(sync.subprocess, 'Popen', side_effect=[sender, receiver]) as spawn:
                    if send_rc or receive_rc:
                        with self.assertRaisesRegex(RuntimeError, 'Transfer failed'):
                            sync.transfer(['zfs', 'send', '-w', 'workspace/data@snap'], 'workspace-backup1/data', 'on')
                    else:
                        sync.transfer(['zfs', 'send', '-w', 'workspace/data@snap'], 'workspace-backup1/data', 'on')
                    args = spawn.call_args_list[1].args[0]
                    self.assertIn('-s', args)
                    self.assertIn('-u', args)
                    self.assertIn('readonly=on', args)
                    self.assertNotIn('-F', args)
                    sender.wait.assert_called_once()
                    receiver.wait.assert_called_once()

    def test_interruption_stops_and_reaps_both_children(self):
        sender, receiver = MagicMock(), MagicMock()
        sender.poll.return_value = receiver.poll.return_value = None
        receiver.wait.side_effect = [KeyboardInterrupt, 1]
        with patch.object(sync.subprocess, 'Popen', side_effect=[sender, receiver]):
            with self.assertRaises(KeyboardInterrupt):
                sync.transfer(['zfs', 'send', '-w', 'workspace/data@snap'], 'workspace-backup1/data', 'on')
        sender.terminate.assert_called_once()
        receiver.terminate.assert_called_once()
        sender.wait.assert_called_once()
        self.assertEqual(receiver.wait.call_count, 2)

    def test_failed_receiver_start_closes_pipe_and_reaps_sender(self):
        sender = MagicMock()
        sender.poll.return_value = None
        with patch.object(sync.subprocess, 'Popen', side_effect=[sender, OSError('spawn failed')]):
            with self.assertRaises(OSError):
                sync.transfer(['zfs', 'send', '-w', 'workspace/data@snap'], 'workspace-backup1/data', 'on')
        sender.stdout.close.assert_called_once()
        sender.terminate.assert_called_once()
        sender.wait.assert_called_once()
