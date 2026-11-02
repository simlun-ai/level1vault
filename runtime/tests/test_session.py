"""Exercise managed session creation and cleanup without a running systemd."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "workspace-session"
loader = importlib.machinery.SourceFileLoader("workspace_session", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None and spec.loader is not None
session = importlib.util.module_from_spec(spec)
loader.exec_module(session)


class SessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.calls: list[list[str]] = []
        self.current = "absent"
        self.sessions = "0"
        self.tmux_fails = False
        self.stop_fails = False
        self.stays_active = False
        self.show_fails = False
        self.check_fails = False
        self.workspace_exists = False
        for name, value in [("LOCK", str(Path(temporary.name) / "lock"))]:
            context = patch.object(session, name, value)
            context.start()
            self.addCleanup(context.stop)
        context = patch.object(session.subprocess, "run", side_effect=self.command)
        context.start()
        self.addCleanup(context.stop)

    def command(self, args: list[str], **kwargs: object):
        self.calls.append(args)
        output, status = "", 0
        if args[:2] == ["systemctl", "show"]:
            output = (
                "LoadState=not-found\nActiveState=inactive\n"
                if self.current == "absent"
                else f"LoadState=loaded\nActiveState={self.current}\n"
            )
            status = int(self.show_fails)
        elif args[0] == "tmux":
            output, status = self.sessions, int(self.tmux_fails)
        elif args[:2] == ["systemctl", "stop"]:
            status = int(self.stop_fails)
            if not self.stays_active:
                self.current = "absent"
        elif args[0] == "systemd-run":
            self.current = "active"
        elif "has-session" in args:
            status = 0 if self.workspace_exists else 1
        elif "new-session" in args:
            self.workspace_exists = True
        elif args[-1] == "check":
            status = int(self.check_fails)
        if status and kwargs.get("check"):
            raise subprocess.CalledProcessError(status, args)
        return subprocess.CompletedProcess(args, status, output, "")

    def test_prepare_uses_one_managed_server_and_reattaches_without_restarting(self):
        session.prepare()
        session.prepare()
        starts = [c for c in self.calls if c[0] == "systemd-run"]
        self.assertEqual(len(starts), 1)
        command = starts[0]
        for option in (
            "--uid=simlun",
            "--gid=simlun",
            "--service-type=exec",
            "--property=KillMode=control-group",
            "--property=TimeoutStopSec=5s",
            "--property=SendSIGKILL=yes",
            "--property=RuntimeDirectoryMode=0700",
        ):
            self.assertIn(option, command)
        self.assertEqual(command[-4:], ["/usr/bin/tmux", "-D", "-S", session.SOCKET])
        panes = [c for c in self.calls if c[0] == "sudo"]
        self.assertEqual(len([c for c in panes if "new-session" in c]), 1)
        self.assertTrue(all("-N" in c for c in panes))
        self.assertNotIn(["systemctl", "stop", session.UNIT], self.calls)

    def test_new_session_after_declined_export_reuses_server(self):
        self.current = "active"
        session.prepare()
        self.assertTrue(self.workspace_exists)
        self.assertFalse(any(c[0] == "systemd-run" for c in self.calls))

    def test_home_check_failure_never_starts_server(self):
        self.check_fails = True
        with self.assertRaises(subprocess.CalledProcessError):
            session.prepare()
        self.assertFalse(any(c[0] == "systemd-run" for c in self.calls))

    def test_socket_readiness_timeout_does_not_create_an_unmanaged_server(self):
        self.tmux_fails = True
        with patch.object(session.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "did not become ready"):
                session.prepare()
        self.assertFalse(any(c[0] == "sudo" for c in self.calls))
        self.assertTrue(all("-N" in c for c in self.calls if c[0] == "tmux"))

    def test_detached_or_extra_sessions_block_cleanup(self):
        self.current = "active"
        for count in ("1", "2"):
            self.sessions = count
            with self.assertRaisesRegex(RuntimeError, "reattach"):
                session.stop()
        self.assertFalse(any(c[:2] == ["systemctl", "stop"] for c in self.calls))

    def test_empty_server_and_agents_are_stopped_and_retry_is_idempotent(self):
        self.current = "active"
        session.stop()
        session.stop()
        self.assertEqual(self.calls.count(["systemctl", "stop", session.UNIT]), 1)

    def test_inspection_failures_never_stop_processes(self):
        self.current = "active"
        for count in ("", "garbage", "-1"):
            self.sessions = count
            with self.assertRaises(RuntimeError):
                session.stop()
        self.tmux_fails = True
        with self.assertRaises(subprocess.CalledProcessError):
            session.stop()
        self.assertFalse(any(c[:2] == ["systemctl", "stop"] for c in self.calls))

    def test_systemd_inspection_failure_never_treated_as_absent(self):
        self.show_fails = True
        with self.assertRaises(subprocess.CalledProcessError):
            session.stop()

    def test_stop_failure_or_surviving_unit_blocks_unmount(self):
        self.current = "active"
        self.stop_fails = True
        with self.assertRaises(subprocess.CalledProcessError):
            session.stop()
        self.stop_fails = False
        self.current = "active"
        self.stays_active = True
        with self.assertRaisesRegex(RuntimeError, "did not stop"):
            session.stop()

    def test_transitional_unit_never_stopped_or_reused(self):
        self.current = "deactivating"
        with self.assertRaisesRegex(RuntimeError, "refusing cleanup"):
            session.stop()
        with self.assertRaisesRegex(RuntimeError, "deactivating"):
            session.prepare()
