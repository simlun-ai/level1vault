"""Exercise home initialization with real temporary files and mocked mount operations."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "workspace-home"
loader = importlib.machinery.SourceFileLoader("workspace_home", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None and spec.loader is not None
home = importlib.util.module_from_spec(spec)
loader.exec_module(home)


class HomeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.workspace = base / "workspace"
        self.home = base / "live-home"
        self.defaults = base / "image-defaults"
        for path in (self.workspace, self.home, self.defaults):
            path.mkdir()
        (self.defaults / ".zshrc").write_text("image defaults")
        (self.defaults / "level1vault").mkdir()
        (self.defaults / "level1vault/README.md").write_text("offline checkout")
        self.dataset = "level1-workspace-1/data"
        self.mounts = {str(self.workspace): self.record("/")}
        self.calls: list[list[str]] = []
        self.process_status = 1
        self.umount_status = 0
        self.properties = {"readonly": "off", "org.level1vault:state": "active"}
        for name, value in [
            ("WORKSPACE", self.workspace),
            ("HOME", self.home),
            ("DEFAULTS", self.defaults),
            ("UID", os.getuid()),
            ("GID", os.getgid()),
        ]:
            context = patch.object(home, name, value)
            context.start()
            self.addCleanup(context.stop)
        context = patch.object(home.subprocess, "run", side_effect=self.command)
        context.start()
        self.addCleanup(context.stop)

        def read_property(args: list[str], **kw: object):
            return self.properties[args[5]]

        context = patch.object(
            home.subprocess,
            "check_output",
            side_effect=read_property,
        )
        context.start()
        self.addCleanup(context.stop)

    def record(self, root: str, dataset: str = "level1-workspace-1/data"):
        return dict(source=dataset, fstype="zfs", fsroot=root)

    def command(self, args: list[str], **kwargs: object):
        self.calls.append(args)
        status, output = 0, ""
        if args[0] == "findmnt":
            record = self.mounts.get(args[4])
            status, output = (
                (0, json.dumps({"filesystems": [record]})) if record else (1, "")
            )
        elif args[0] == "pgrep":
            status = self.process_status
        elif args[0] == "mount":
            self.mounts[str(self.home)] = self.record("/home/simlun")
        elif args[0] == "umount":
            status = self.umount_status
            if not status:
                self.mounts.pop(str(self.home))
        return subprocess.CompletedProcess(args, status, output, "")

    def test_first_mount_seeds_defaults_and_remount_preserves_edits(self):
        home.attach(self.dataset)
        persistent = self.workspace / "home/simlun"
        self.assertEqual(
            (persistent / "level1vault/README.md").read_text(), "offline checkout"
        )
        self.assertEqual(persistent.stat().st_mode & 0o777, 0o700)
        (persistent / ".zshrc").write_text("my configuration")
        (self.defaults / ".zshrc").write_text("new image defaults")
        home.detach("level1-workspace-1")
        home.attach(self.dataset)
        self.assertEqual((persistent / ".zshrc").read_text(), "my configuration")
        self.assertEqual(len([c for c in self.calls if c[0] == "chown"]), 1)

    def test_failed_seed_never_publishes_partial_home(self):
        with patch.object(home.shutil, "copytree", side_effect=OSError("full disk")):
            with self.assertRaisesRegex(OSError, "full disk"):
                home.attach(self.dataset)
        self.assertEqual(list((self.workspace / "home").iterdir()), [])
        self.assertFalse(any(c[0] == "mount" for c in self.calls))

    def test_symlinked_home_is_rejected_without_copy_or_mount(self):
        (self.workspace / "home").symlink_to(self.defaults, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "real directory"):
            home.attach(self.dataset)
        self.assertFalse(any(c[0] in ("mount", "chown") for c in self.calls))

    def test_foreign_mount_is_never_covered(self):
        self.mounts[str(self.home)] = self.record(
            "/home/simlun", "level1-workspace-2/data"
        )
        with self.assertRaisesRegex(RuntimeError, "Unexpected"):
            home.attach(self.dataset)
        self.assertFalse(any(c[0] == "mount" for c in self.calls))

    def test_existing_attachment_is_idempotent_even_with_tmux_running(self):
        home.attach(self.dataset)
        self.process_status = 0
        home.attach(self.dataset)
        self.assertEqual(len([c for c in self.calls if c[0] == "mount"]), 1)

    def test_running_user_blocks_home_switch_and_detach(self):
        self.process_status = 0
        with self.assertRaisesRegex(RuntimeError, "running processes"):
            home.attach(self.dataset)
        self.assertIn(["pgrep", "-u", str(os.getuid())], self.calls)
        self.process_status = 1
        home.attach(self.dataset)
        self.process_status = 0
        with self.assertRaisesRegex(RuntimeError, "running processes"):
            home.detach("level1-workspace-1")
        self.assertFalse(any(c[0] == "umount" for c in self.calls))

    def test_process_inspection_failure_blocks_detach(self):
        home.attach(self.dataset)
        self.process_status = 2
        with self.assertRaisesRegex(RuntimeError, "Cannot check"):
            home.detach("level1-workspace-1")

    def test_busy_home_never_uses_forced_or_lazy_unmount(self):
        home.attach(self.dataset)
        self.umount_status = 1
        with self.assertRaisesRegex(RuntimeError, "busy"):
            home.detach("level1-workspace-1")
        self.assertIn(["umount", str(self.home)], self.calls)

    def test_exporting_another_pool_keeps_active_home_attached(self):
        home.attach(self.dataset)
        home.detach("level1-workspace-2")
        self.assertIn(str(self.home), self.mounts)
        self.assertFalse(any(c[0] == "umount" for c in self.calls))

    def test_start_requires_matching_home_and_active_writable_dataset(self):
        with self.assertRaisesRegex(RuntimeError, "Unexpected"):
            home.check()
        home.attach(self.dataset)
        home.check()
        for prop, value in [("readonly", "on"), ("org.level1vault:state", "replica")]:
            with patch.dict(self.properties, {prop: value}):
                with self.assertRaisesRegex(RuntimeError, "writable active"):
                    home.check()

    def test_replica_without_home_explains_backup_inspection(self):
        self.dataset = "level1-workspace-2/data"
        self.mounts[str(self.workspace)] = self.record("/", self.dataset)
        self.properties = {"readonly": "on", "org.level1vault:state": "replica"}
        with self.assertRaisesRegex(RuntimeError, "Pair 2 is a read-only backup") as error:
            home.check()
        message = str(error.exception)
        self.assertIn("/mnt/workspace/home/simlun", message)
        self.assertIn("cd / && workspace-umount 2", message)
        self.assertFalse(any(c[0] in ("mount", "chown") for c in self.calls))
