"""Exercise full Git bundles and offline updates with real disposable repositories."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "common"))
import git_bundles as bundles  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "import_git_bundles", str(ROOT / "runtime/workspace-transport-import-git-bundles")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
receiver = importlib.util.module_from_spec(spec)
loader.exec_module(receiver)


class GitBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.remote = self.base / "upstream/project.git"
        self.remote.mkdir(parents=True)
        self.fixture("init", "-b", "main")
        self.fixture("config", "user.name", "Fixture")
        self.fixture("config", "user.email", "fixture@example.invalid")
        self.commit("first")
        self.fixture("tag", "v1")
        self.fixture("checkout", "-b", "feature")
        self.commit("feature work")
        self.fixture("checkout", "main")
        self.outbox = self.base / "transport/git-bundles"
        self.destination = self.base / "workspace/git"

    def fixture(self, *args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(self.remote), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()

    def commit(self, value: str) -> None:
        (self.remote / "file").write_text(value)
        self.fixture("add", ".")
        self.fixture("commit", "-m", value)

    def prepare(self) -> Path:
        return bundles.add_bundle(self.remote.as_uri(), self.outbox)

    def restore(self) -> Path:
        self.prepare()
        self.assertTrue(bundles.import_bundles(self.outbox, self.destination))
        return self.destination / "project"

    def test_initial_restore_has_full_history_branches_tags_and_working_files(
        self,
    ) -> None:
        repo = self.restore()
        self.assertEqual((repo / "file").read_text(), "first")
        self.assertEqual(bundles.text(repo, "symbolic-ref", "HEAD"), "refs/heads/main")
        self.assertEqual(
            bundles.text(repo, "rev-parse", "refs/remotes/origin/feature"),
            self.fixture("rev-parse", "feature"),
        )
        self.assertEqual(
            bundles.text(repo, "rev-parse", "v1"), self.fixture("rev-parse", "v1")
        )
        self.assertEqual(
            bundles.text(repo, "rev-parse", "--is-shallow-repository"), "false"
        )
        self.assertTrue((self.outbox / "project.bundle").is_file())

    def test_updates_are_repeatable_and_refresh_new_branches_and_tags(self) -> None:
        repo = self.restore()
        self.commit("second")
        self.fixture("branch", "new-branch")
        self.fixture("tag", "-a", "v2", "-m", "annotated tag")
        self.prepare()
        for _ in range(2):
            self.assertTrue(bundles.import_bundles(self.outbox, self.destination))
            self.assertEqual((repo / "file").read_text(), "second")
            self.assertEqual(
                bundles.text(repo, "rev-parse", "refs/remotes/origin/new-branch"),
                self.fixture("rev-parse", "main"),
            )
            self.assertEqual(
                bundles.text(repo, "rev-parse", "v2"), self.fixture("rev-parse", "v2")
            )

    def test_multiple_repositories_and_name_collision(self) -> None:
        self.prepare()
        other = self.base / "other.git"
        subprocess.run(
            ["git", "clone", "--bare", str(self.remote), str(other)],
            check=True,
            capture_output=True,
        )
        bundles.add_bundle(other.as_uri(), self.outbox)
        self.assertTrue(bundles.import_bundles(self.outbox, self.destination))
        self.assertTrue((self.destination / "other/file").is_file())
        collision = self.base / "different/project.git"
        collision.parent.mkdir()
        subprocess.run(
            ["git", "clone", "--bare", str(self.remote), str(collision)],
            check=True,
            capture_output=True,
        )
        before = (self.outbox / "project.bundle").read_bytes()
        with self.assertRaisesRegex(ValueError, "collision"):
            bundles.add_bundle(collision.as_uri(), self.outbox)
        self.assertEqual((self.outbox / "project.bundle").read_bytes(), before)

    def test_failed_preparation_preserves_previous_bundle_and_metadata(self) -> None:
        path = self.prepare()
        before = (path.read_bytes(), path.with_suffix(".json").read_bytes())
        self.fixture("symbolic-ref", "HEAD", "refs/heads/missing")
        with self.assertRaisesRegex(ValueError, "default branch"):
            self.prepare()
        self.assertEqual(
            (path.read_bytes(), path.with_suffix(".json").read_bytes()), before
        )

    def test_corrupt_bundle_preflight_changes_no_destination(self) -> None:
        path = self.prepare()
        path.write_bytes(path.read_bytes()[:100])
        with self.assertRaisesRegex(ValueError, "checksum"):
            bundles.import_bundles(self.outbox, self.destination)
        self.assertFalse((self.destination / "project").exists())

    def test_incremental_bundle_is_refused_even_with_matching_metadata_hash(
        self,
    ) -> None:
        path = self.prepare()
        old = self.fixture("rev-parse", "HEAD")
        self.commit("next")
        self.fixture("bundle", "create", str(path), old + "..main")
        meta = json.loads(path.with_suffix(".json").read_text())
        meta["sha256"] = bundles.digest(path)
        path.with_suffix(".json").write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, "prerequisite"):
            bundles.import_bundles(self.outbox, self.destination)

    def test_dirty_untracked_and_ignored_files_are_preserved(self) -> None:
        repo = self.restore()
        self.commit("new upstream")
        self.prepare()
        for name in ("file", "untracked", ".ignored"):
            with self.subTest(name=name):
                path = repo / name
                before = path.read_bytes() if path.exists() else None
                path.write_text("local work")
                if name == ".ignored":
                    (repo / ".git/info").mkdir(exist_ok=True)
                    (repo / ".git/info/exclude").write_text(".ignored\n")
                with self.assertRaisesRegex(ValueError, "Local changes"):
                    bundles.import_bundles(self.outbox, self.destination)
                self.assertEqual(path.read_text(), "local work")
                if before is None:
                    path.unlink()
                else:
                    path.write_bytes(before)

    def test_local_commits_and_old_bundles_are_refused_without_rollback(self) -> None:
        repo = self.restore()
        original_bundle = (self.outbox / "project.bundle").read_bytes()
        original_meta = (self.outbox / "project.json").read_bytes()
        self.commit("new upstream")
        self.prepare()
        self.assertTrue(bundles.import_bundles(self.outbox, self.destination))
        (self.outbox / "project.bundle").write_bytes(original_bundle)
        (self.outbox / "project.json").write_bytes(original_meta)
        with self.assertRaisesRegex(ValueError, "older bundle"):
            bundles.import_bundles(self.outbox, self.destination)
        self.assertEqual((repo / "file").read_text(), "new upstream")
        bundles.git(
            repo,
            "-c",
            "user.name=Local",
            "-c",
            "user.email=local@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "local work",
        )
        self.prepare()
        with self.assertRaisesRegex(ValueError, "Local commits"):
            bundles.import_bundles(self.outbox, self.destination)

    def test_different_checkout_default_branch_and_source_are_refused(self) -> None:
        repo = self.restore()
        bundles.git(repo, "checkout", "-b", "local-branch")
        with self.assertRaisesRegex(ValueError, "another branch"):
            bundles.import_bundles(self.outbox, self.destination)
        bundles.git(repo, "checkout", "main")
        bundles.git(repo, "config", "level1vault.transport-source", "another-source")
        with self.assertRaisesRegex(ValueError, "identity"):
            bundles.import_bundles(self.outbox, self.destination)
        bundles.git(
            repo,
            "config",
            "level1vault.transport-source",
            bundles.source_identity(self.remote.as_uri())[1],
        )
        self.fixture("symbolic-ref", "HEAD", "refs/heads/feature")
        self.prepare()
        with self.assertRaisesRegex(ValueError, "Default branch changed"):
            bundles.import_bundles(self.outbox, self.destination)

    def test_changed_deleted_tags_and_local_branches_are_preserved(self) -> None:
        repo = self.restore()
        original = bundles.text(repo, "rev-parse", "v1")
        bundles.git(repo, "branch", "local-branch")
        self.commit("new tag target")
        self.fixture("tag", "-f", "v1")
        self.fixture("branch", "-D", "feature")
        self.prepare()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertTrue(bundles.import_bundles(self.outbox, self.destination))
        self.assertIn("preserved changed/deleted tag", output.getvalue())
        self.assertEqual(bundles.text(repo, "rev-parse", "v1"), original)
        self.assertEqual(bundles.text(repo, "rev-parse", "local-branch"), original)
        self.assertNotIn(
            "refs/remotes/origin/feature",
            bundles.refs(repo, "refs/remotes/origin", tags=False),
        )
        self.fixture("tag", "-d", "v1")
        self.prepare()
        self.assertTrue(bundles.import_bundles(self.outbox, self.destination))
        self.assertEqual(bundles.text(repo, "rev-parse", "v1"), original)

    def test_interrupted_update_retries_after_fetch_without_duplicate_checkout(
        self,
    ) -> None:
        repo = self.restore()
        self.commit("updated")
        self.prepare()
        original = bundles.git

        def fail_merge(
            directory: Path, *args: str, offline: bool = True, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            if args[0] == "merge":
                raise OSError("interrupted")
            return original(directory, *args, offline=offline, check=check)

        with patch.object(bundles, "git", side_effect=fail_merge):
            self.assertFalse(bundles.import_bundles(self.outbox, self.destination))
        self.assertEqual((repo / "file").read_text(), "first")
        self.assertTrue(bundles.import_bundles(self.outbox, self.destination))
        self.assertEqual((repo / "file").read_text(), "updated")

    def test_symlink_bundle_destination_and_mismatched_refs_are_refused(self) -> None:
        path = self.prepare()
        original = self.base / "original.bundle"
        path.rename(original)
        path.symlink_to(original)
        with self.assertRaisesRegex(ValueError, "regular bundle"):
            bundles.import_bundles(self.outbox, self.destination)
        path.unlink()
        original.rename(path)
        self.destination.mkdir(exist_ok=True)
        (self.destination / "project").symlink_to(self.remote)
        with self.assertRaisesRegex(ValueError, "standalone"):
            bundles.import_bundles(self.outbox, self.destination)
        (self.destination / "project").unlink()
        meta = json.loads(path.with_suffix(".json").read_text())
        del meta["refs"]["refs/heads/feature"]
        path.with_suffix(".json").write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, "refs differ"):
            bundles.import_bundles(self.outbox, self.destination)

    def test_offline_git_disables_network_hooks_global_filters_and_submodule_fetch(
        self,
    ) -> None:
        repo = self.restore()
        marker = self.base / "hook-ran"
        hook = repo / ".git/hooks/post-merge"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        hook.chmod(0o755)
        (self.remote / ".gitmodules").write_text(
            '[submodule "external"]\npath = external\nurl = https://example.invalid/external.git\n'
        )
        self.commit("new with submodule metadata")
        self.prepare()
        self.assertTrue(bundles.import_bundles(self.outbox, self.destination))
        self.assertFalse(marker.exists())
        result = bundles.git(
            repo, "ls-remote", "https://example.invalid/repo", check=False
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not allowed", result.stderr)
        bundles.git(repo, "config", "filter.trap.smudge", "false")
        with self.assertRaisesRegex(ValueError, "filters"):
            bundles.import_bundles(self.outbox, self.destination)

    def test_batch_preflight_failure_leaves_other_repositories_unchanged(self) -> None:
        repo = self.restore()
        self.commit("pending update")
        self.prepare()
        other = self.base / "other.git"
        subprocess.run(
            ["git", "clone", "--bare", str(self.remote), str(other)],
            check=True,
            capture_output=True,
        )
        bad = bundles.add_bundle(other.as_uri(), self.outbox)
        bad.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum"):
            bundles.import_bundles(self.outbox, self.destination)
        self.assertEqual((repo / "file").read_text(), "first")
        self.assertFalse((self.destination / "other").exists())

    def test_make_requires_repo_before_any_clone(self) -> None:
        result = subprocess.run(
            ["make", "add-transport-git-bundle", "REPO="],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set REPO", result.stderr)


class IdentityAndWorkspaceTests(unittest.TestCase):
    def test_names_and_identity_support_https_ssh_and_scp(self) -> None:
        for url in (
            "https://github.com/owner/repo.git",
            "ssh://git@github.com/owner/repo.git",
            "git@github.com:owner/repo.git",
        ):
            self.assertEqual(
                bundles.source_identity(url), ("repo", "github.com/owner/repo")
            )
        for url in (
            "-option",
            "https://user:secret@host/repo.git",
            "https://host/repo.git?token=secret",
            "https://host/../",
            "ext::command",
            "https://host/.hidden",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                bundles.source_identity(url)

    def test_receiver_requires_active_workspace_and_inbox_source(self) -> None:
        source = Path("/mnt/workspace/inbox/delivery/git-bundles")

        def property_value(_dataset: str, name: str) -> str:
            return "active" if name.endswith(":state") else "off"

        with (
            patch.object(receiver, "output", return_value="level1-workspace-1/data"),
            patch.object(
                receiver, "validate_pair", return_value="level1-workspace-1/data"
            ),
            patch.object(
                receiver,
                "prop",
                side_effect=property_value,
            ),
            patch.object(Path, "is_dir", return_value=True),
        ):
            self.assertEqual(
                receiver.validate_workspace(source), Path("/mnt/workspace/git")
            )
            with self.assertRaisesRegex(ValueError, "inbox"):
                receiver.validate_workspace(Path("/tmp/git-bundles"))
            with (
                patch.object(receiver, "prop", return_value="replica"),
                self.assertRaisesRegex(ValueError, "active writable"),
            ):
                receiver.validate_workspace(source)
        with (
            patch.object(receiver, "output", return_value="/dev/other"),
            self.assertRaisesRegex(ValueError, "Mount"),
        ):
            receiver.validate_workspace(source)
