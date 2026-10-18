"""Retention must preserve recovery history and never delete during a preview."""
import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('retention', str(Path(__file__).resolve().parents[1] / 'workspace-sync'))
spec = importlib.util.spec_from_loader(loader.name, loader)
sync = importlib.util.module_from_spec(spec)
loader.exec_module(sync)
POOL = 'level1-workspace-1'
DATA = POOL + '/data'
OTHER = 'level1-workspace-2'


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.entries = [(DATA + f'@l1v-20261018T120000Z-{n:08x}', str(n)) for n in range(1, 9)]
        self.user = DATA + '@before-upgrade'
        self.entries.append((self.user, 'user'))
        self.properties = {
            (POOL, sync.PROPERTY + 'pair'): '1',
            (POOL, sync.PROPERTY + 'state'): 'active',
            (DATA, 'readonly'): 'off',
            (OTHER, sync.PROPERTY + 'pending-snapshot'): self.entries[3][0],
            (OTHER, sync.PROPERTY + 'pending-guid'): '4',
            (self.entries[2][0], 'clones'): 'another-pool/clone',
            (self.entries[4][0], 'defer_destroy'): 'on',
        }
        self.properties.update({(name, 'guid'): guid for name, guid in self.entries})
        self.held = {self.entries[1][0]}
        patches = [patch.object(sync, 'run', side_effect=self.query),
                   patch.object(sync, 'prop', side_effect=lambda obj, key: self.properties.get((obj, key), '-')),
                   patch.object(sync, 'validate_data'),
                   patch.object(sync, 'snapshots', side_effect=lambda data: list(self.entries))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def query(self, *args):
        if args == ('zpool', 'list', '-H', '-o', 'name'):
            return POOL + '\n' + OTHER
        if args[:3] == ('zfs', 'holds', '-H'):
            return 'held' if args[3] in self.held else ''
        raise AssertionError(args)

    def test_preview_and_apply_preserve_recent_held_pending_clone_and_user_snapshots(self):
        with patch.object(sync, 'command') as command:
            sync.prune('1', 2)
            command.assert_not_called()
            sync.prune('1', 2, apply=True)
        self.assertEqual([c.args for c in command.call_args_list],
                         [('zfs', 'destroy', self.entries[i][0]) for i in (0, 5)])

    def test_replica_and_pending_guid_after_source_rename_are_protected(self):
        self.properties[(POOL, sync.PROPERTY + 'state')] = 'replica'
        self.properties[(DATA, 'readonly')] = 'on'
        self.properties[(OTHER, sync.PROPERTY + 'pending-snapshot')] = 'old-name/data@checkpoint'
        plan = sync.retention_plan('1', 1)
        self.assertIn((*self.entries[3], 'pending checkpoint'), plan)
        self.assertIn((self.user, 'user', 'user snapshot'), plan)

    def test_incomplete_receive_or_inconsistent_role_refuses_all_deletion(self):
        for key, value in [((DATA, 'receive_resume_token'), 'resume'),
                           ((POOL, sync.PROPERTY + 'pending-snapshot'), 'pending'),
                           ((POOL, sync.PROPERTY + 'state'), 'activating'),
                           ((POOL, sync.PROPERTY + 'pair'), '2')]:
            with self.subTest(key=key), patch.dict(self.properties, {key: value}), \
                    patch.object(sync, 'command') as command:
                with self.assertRaises(RuntimeError):
                    sync.prune('1', 1, apply=True)
                command.assert_not_called()

    def test_changed_guid_or_new_hold_aborts_before_destroy(self):
        original = sync.retention_plan
        for change in ('guid', 'hold'):
            calls = 0
            def observe(*args):
                nonlocal calls
                calls += 1
                if calls == 2:
                    if change == 'hold':
                        self.held.add(self.entries[0][0])
                    else:
                        self.entries[0] = (self.entries[0][0], 'replacement')
                return original(*args)
            with self.subTest(change=change), patch.object(sync, 'retention_plan', side_effect=observe), \
                    patch.object(sync, 'command') as command:
                with self.assertRaisesRegex(RuntimeError, '[Ss]tate changed'):
                    sync.prune('1', 2, apply=True)
                command.assert_not_called()

    def test_bad_keep_unimported_pool_and_failed_inspection_never_delete(self):
        with patch.object(sync, 'command') as command:
            for keep in (0, -1):
                with self.assertRaises(RuntimeError):
                    sync.prune('1', keep, apply=True)
            with patch.object(sync, 'run', return_value=''):
                with self.assertRaisesRegex(RuntimeError, 'Import'):
                    sync.prune('1', 1, apply=True)
            with patch.object(sync, 'run', side_effect=subprocess.CalledProcessError(1, 'zfs')):
                with self.assertRaises(subprocess.CalledProcessError):
                    sync.prune('1', 1, apply=True)
            command.assert_not_called()

    def test_cli_preview_uses_workspace_lock_without_importing_or_preparing_host(self):
        with patch.object(sync.sys, 'argv', ['workspace-sync', '--prune', '1', '--keep', '2']), \
                patch.object(sync.os, 'geteuid', return_value=0), \
                patch.object(sync, 'open'), patch.object(sync.fcntl, 'flock') as lock, \
                patch.object(sync, 'command') as command:
            sync.main()
        lock.assert_called_once()
        command.assert_not_called()
