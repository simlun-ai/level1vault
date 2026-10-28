"""Exercise full-history offline checkouts using real disposable Git repositories."""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "repository.py"
spec = importlib.util.spec_from_file_location("live_repository", SCRIPT)
assert spec is not None and spec.loader is not None
repository = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repository)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.source = self.base / "source"
        self.source.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.source / "guide").write_text("first\n")
        self.git("add", "guide")
        self.git("commit", "-m", "First revision")
        self.first = self.git("rev-parse", "HEAD")
        self.git("tag", "v1")
        self.git("checkout", "-b", "other")
        (self.source / "other").write_text("branch history\n")
        self.git("add", "other")
        self.git("commit", "-m", "Other branch")
        self.other = self.git("rev-parse", "HEAD")
        self.git("checkout", "main")
        (self.source / "guide").write_text("second\n")
        self.git("commit", "-am", "Second revision")
        self.head = self.git("rev-parse", "HEAD")

    def git(self, *args: str | Path, cwd: Path | None = None):
        return repository.git(*args, cwd=cwd or self.source)

    def test_full_history_tags_and_branches_survive_without_local_configuration(self):
        self.git("config", "http.extraheader", "fixture-secret")
        self.git(
            "config",
            "remote.origin.url",
            "https://fixture-secret@example.invalid/private",
        )
        (self.source / "guide").write_text("uncommitted changes\n")
        (self.source / "private.img").write_text("private build artifact\n")
        package = self.base / "package"
        checkout = self.base / "checkout"
        repository.bundle(self.source, package)
        repository.install(package, checkout)

        self.assertEqual(self.git("rev-parse", "HEAD", cwd=checkout), self.head)
        self.assertEqual(self.git("show", "v1:guide", cwd=checkout), "first")
        self.assertEqual(
            self.git("show", self.other + ":other", cwd=checkout), "branch history"
        )
        self.assertEqual((checkout / "guide").read_text(), "second\n")
        self.assertFalse((checkout / "private.img").exists())
        self.assertNotIn("fixture-secret", (checkout / ".git/config").read_text())
        self.assertEqual(
            self.git("remote", "get-url", "origin", cwd=checkout), repository.ORIGIN
        )
        self.assertFalse((checkout / ".git/objects/info/alternates").exists())
        self.assertEqual(
            self.git("rev-parse", "--is-shallow-repository", cwd=checkout), "false"
        )
        self.assertEqual(self.git("status", "--porcelain", cwd=checkout), "")

    def test_detached_build_commit_survives_without_a_branch(self):
        self.git("checkout", "--detach")
        self.git("commit", "--allow-empty", "-m", "Detached build revision")
        head = self.git("rev-parse", "HEAD")
        package = self.base / "package"
        checkout = self.base / "checkout"
        repository.bundle(self.source, package)
        repository.install(package, checkout)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=checkout), head)

    def test_shallow_source_is_rejected_before_creating_output(self):
        shallow = self.base / "shallow"
        self.git("clone", "--depth=1", self.source.as_uri(), shallow)
        package = self.base / "package"
        with self.assertRaisesRegex(SystemExit, "git fetch --unshallow"):
            repository.bundle(shallow, package)
        self.assertFalse(package.exists())

    def test_existing_checkout_is_preserved(self):
        package = self.base / "package"
        repository.bundle(self.source, package)
        checkout = self.base / "checkout"
        checkout.mkdir()
        marker = checkout / "keep"
        marker.write_text("existing data")
        with self.assertRaises(subprocess.CalledProcessError):
            repository.install(package, checkout)
        self.assertEqual(marker.read_text(), "existing data")
