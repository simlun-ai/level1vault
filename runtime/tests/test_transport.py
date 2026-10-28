"""Transport integrity and failure ordering without privileged ZFS IO."""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "common"))
import transport as t  # noqa: E402 - resolve shared helpers after configuring their path


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / "source"
        self.source.mkdir()
        (self.source / "README.md").write_text("instructions")
        (self.source / ".hidden").write_bytes(b"\x00\xff")
        (self.source / "empty").mkdir()
        (self.source / "nested").mkdir()
        (self.source / "nested" / "name\nwith spaces").write_text("payload")
        (self.source / "outside").symlink_to("/etc/shadow")
        (self.source / "linked-dir").symlink_to("nested", target_is_directory=True)
        self.package = self.base / "package"
        self.package.mkdir()
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.manifest = t.package(self.source, self.package, t.walk_tree(self.source))
        self.commands = self.contexts.enter_context(patch.object(t, "command"))
        self.contexts.enter_context(patch.object(t.os, "chown"))
        # Exercise real file IO as the test user, without requiring UID 0 or 1000.
        original = t.private_directory
        self.contexts.enter_context(
            patch.object(
                t,
                "private_directory",
                side_effect=lambda path, owner=0: original(path, os.getuid()),
            )
        )

    def test_roundtrip_includes_readme_hidden_empty_and_inert_symlinks(self):
        self.assertEqual(t.read_package(self.package), self.manifest)
        final = t.publish_inbox(self.package, self.workspace, "pool")
        self.assertEqual(t.walk_tree(final), self.manifest["entries"])
        self.assertEqual((final / "outside").readlink(), Path("/etc/shadow"))
        self.assertTrue((final / "empty").is_dir())
        self.assertEqual((final / "README.md").read_text(), "instructions")
        self.assertTrue((self.package / "files").is_dir())
        receipt = final.parent / ".transport-receipts" / (final.name + ".json")
        self.assertEqual(json.loads(receipt.read_text()), self.manifest)

    def test_file_corruption_missing_extra_and_link_changes_are_rejected(self):
        for change in ("corrupt", "missing", "extra", "link"):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as directory,
            ):
                copy = Path(directory) / "copy"
                t.shutil.copytree(self.package, copy, symlinks=True)
                if change == "corrupt":
                    (copy / "files/README.md").write_text("changed")
                elif change == "missing":
                    (copy / "files/README.md").unlink()
                elif change == "extra":
                    (copy / "files/extra").touch()
                else:
                    (copy / "files/outside").unlink()
                    (copy / "files/outside").symlink_to("/different")
                with self.assertRaisesRegex(ValueError, "verification"):
                    t.read_package(copy)

    def test_manifest_mismatch_and_incomplete_package_are_rejected(self):
        (self.package / "manifest.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "checksum"):
            t.read_package(self.package)
        (self.package / "READY").unlink()
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            t.read_package(self.package)

    def test_manifest_paths_are_never_used_to_write_files(self):
        manifest = dict(
            self.manifest, entries=[{"path": "../escape", "type": "directory"}]
        )
        raw = t.canonical(manifest)
        (self.package / "manifest.json").write_bytes(raw)
        (self.package / "READY").write_text(hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(ValueError, "verification"):
            t.publish_inbox(self.package, self.workspace, "pool")
        self.assertFalse((self.workspace / "inbox").exists())
        self.assertFalse((self.base / "escape").exists())

    def test_special_files_refused_and_source_changes_prevent_completion(self):
        fifo = self.source / "fifo"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "Special files"):
            t.walk_tree(self.source)
        fifo.unlink()
        entries = t.walk_tree(self.source)
        (self.source / "README.md").write_text("new version")
        other = self.base / "other"
        other.mkdir()
        with self.assertRaisesRegex(ValueError, "Source changed"):
            t.package(self.source, other, entries)
        self.assertFalse((other / "READY").exists())

    def test_inbox_copy_is_rehashed_before_publication(self):
        original = t.walk_tree

        def copy_with_corruption(source, destination=None):
            result = original(source, destination)
            if destination is not None:
                (destination / "README.md").write_text("corruption")
            return result

        with patch.object(t, "walk_tree", side_effect=copy_with_corruption):
            with self.assertRaisesRegex(ValueError, "Inbox file verification"):
                t.publish_inbox(self.package, self.workspace, "pool")
        self.assertFalse(
            (self.workspace / "inbox" / self.manifest["delivery"]).exists()
        )
        self.assertTrue((self.package / "READY").exists())
        # A retry replaces the partial copy and publishes the original bytes.
        final = t.publish_inbox(self.package, self.workspace, "pool")
        self.assertEqual((final / "README.md").read_text(), "instructions")

    def test_cleanup_retry_reuses_verified_delivery_but_refuses_changed_copy(self):
        final = t.publish_inbox(self.package, self.workspace, "pool")
        self.assertEqual(t.publish_inbox(self.package, self.workspace, "pool"), final)
        self.assertEqual(len(list(final.parent.glob("[0-9]*"))), 1)
        (final / "README.md").write_text("local edits")
        with self.assertRaisesRegex(ValueError, "differs"):
            t.publish_inbox(self.package, self.workspace, "pool")

    def test_unreceipted_collision_symlink_inbox_and_symlink_delivery_refused(self):
        inbox = self.workspace / "inbox"
        inbox.symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "ownership"):
            t.publish_inbox(self.package, self.workspace, "pool")
        inbox.unlink()
        inbox.mkdir(mode=0o700)
        final = inbox / self.manifest["delivery"]
        final.mkdir()
        with self.assertRaisesRegex(ValueError, "differs"):
            t.publish_inbox(self.package, self.workspace, "pool")
        final.rmdir()
        final.symlink_to(self.source)
        with self.assertRaises(ValueError):
            t.publish_inbox(self.package, self.workspace, "pool")

    def test_two_deliveries_do_not_overwrite_same_filenames(self):
        first = t.publish_inbox(self.package, self.workspace, "pool")
        other = self.base / "other"
        other.mkdir()
        (self.source / "README.md").write_text("second delivery")
        t.package(self.source, other, t.walk_tree(self.source))
        second = t.publish_inbox(other, self.workspace, "pool")
        self.assertNotEqual(first, second)
        self.assertEqual((first / "README.md").read_text(), "instructions")
        self.assertEqual((second / "README.md").read_text(), "second delivery")


class ZFSLifecycleTests(unittest.TestCase):
    pool = "level1-workspace-1"
    data = pool + "/data"
    transport = pool + "/transport"

    def setUp(self):
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        self.commands = self.contexts.enter_context(patch.object(t, "command"))
        self.contexts.enter_context(
            patch.object(t, "validate_pair", return_value=self.data)
        )
        self.contexts.enter_context(
            patch.object(t, "validate_transport", return_value=self.transport)
        )
        self.props = {
            "org.level1vault:state": "active",
            "readonly": "off",
            "mounted": "yes",
            "mountpoint": "/mnt/workspace",
            "keystatus": "unavailable",
        }
        self.contexts.enter_context(
            patch.object(t, "prop", side_effect=lambda ds, name: self.props[name])
        )
        self.contexts.enter_context(patch.object(t, "output", side_effect=self.output))
        self.contexts.enter_context(
            patch.object(
                t, "datasets", return_value=[self.pool, self.data, self.transport]
            )
        )

    def output(self, *args):
        if args[:4] == ("zpool", "list", "-H", "-o"):
            return "ONLINE" if args[4] == "health" else self.pool
        if args[0] == "python3" or args[:2] == ("zpool", "get"):
            return "123"
        if args[0] == "findmnt":
            return self.data
        return ""

    @contextmanager
    def mounted(self, *args, **kwargs):
        yield Path("/unused")

    def test_import_orders_publication_unmount_then_exact_destroy(self):
        events = []

        @contextmanager
        def mounted(*args, **kwargs):
            self.assertTrue(kwargs["readonly"])
            events.append("mount")
            yield Path("/unused")
            events.append("unmount")

        self.commands.side_effect = lambda *args: events.append(args)
        with (
            patch.object(t, "mounted_transport", side_effect=mounted),
            patch.object(
                t,
                "publish_inbox",
                side_effect=lambda *args: (
                    events.append("published") or Path("/inbox/delivery")
                ),
            ),
        ):
            t.import_transport("1")
        self.assertLess(events.index("published"), events.index("unmount"))
        self.assertLess(
            events.index("unmount"), events.index(("zfs", "destroy", self.transport))
        )
        self.assertNotIn("-r", repr(self.commands.call_args_list))

    def test_verification_or_unmount_failure_never_destroys_source(self):
        for failure in ("verify", "unmount"):

            @contextmanager
            def mounted(*args, failure=failure, **kwargs):
                yield Path("/unused")
                if failure == "unmount":
                    raise OSError("busy")

            with (
                self.subTest(failure=failure),
                patch.object(t, "mounted_transport", side_effect=mounted),
                patch.object(
                    t,
                    "publish_inbox",
                    side_effect=RuntimeError("verification failed")
                    if failure == "verify"
                    else None,
                ),
            ):
                self.commands.reset_mock()
                with self.assertRaises((RuntimeError, OSError)):
                    t.import_transport("1")
                self.commands.assert_not_called()

    def test_replica_and_unmounted_active_are_refused(self):
        for state, mounted in [("replica", "yes"), ("active", "no")]:
            self.props.update({"org.level1vault:state": state, "mounted": mounted})
            with self.subTest(state=state), self.assertRaises(ValueError):
                t.import_transport("1")
            self.commands.assert_not_called()

    def stage_patches(self):
        self.props["mounted"] = "no"
        self.contexts.enter_context(
            patch.object(t, "walk_tree", return_value=[{"type": "file", "size": 42}])
        )
        self.contexts.enter_context(patch("builtins.input", return_value="yes"))
        self.contexts.enter_context(
            patch.object(
                t,
                "mirror_leaves",
                return_value=[("  ", "1", "ONLINE", "0", "0", "0")] * 2,
            )
        )
        self.contexts.enter_context(
            patch.object(t, "mounted_transport", side_effect=self.mounted)
        )
        self.contexts.enter_context(
            patch.object(t, "package", return_value={"delivery": "test"})
        )
        original = self.output
        self.contexts.enter_context(
            patch.object(
                t,
                "output",
                side_effect=lambda *args: (
                    ""
                    if args == ("zpool", "list", "-H", "-o", "name")
                    else original(*args)
                ),
            )
        )

    def test_stage_replaces_only_transport_and_exports_without_loading_keys(self):
        self.stage_patches()
        t.stage("1", Path("/source"), Path("/discover"))
        calls = [call.args for call in self.commands.call_args_list]
        self.assertIn(("zfs", "destroy", self.transport), calls)
        self.assertEqual(calls[-1], ("zpool", "export", self.pool))
        imp = next(call for call in calls if call[:2] == ("zpool", "import"))
        self.assertIn("-N", imp)
        self.assertNotIn("-l", imp)
        self.assertNotIn("-f", imp)
        self.assertFalse(any("load-key" in call for call in calls))

    def test_stage_locked_data_check_and_copy_failure_export_pool(self):
        self.stage_patches()
        self.props["keystatus"] = "available"
        with self.assertRaisesRegex(ValueError, "locked"):
            t.stage("1", Path("/source"), Path("/discover"))
        self.assertEqual(self.commands.call_args.args, ("zpool", "export", self.pool))
        self.assertNotIn(
            ("zfs", "destroy", self.transport),
            [c.args for c in self.commands.call_args_list],
        )
        self.props["keystatus"] = "unavailable"
        with (
            patch.object(t, "package", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            t.stage("1", Path("/source"), Path("/discover"))
        self.assertEqual(self.commands.call_args.args, ("zpool", "export", self.pool))

    def test_empty_source_and_already_imported_pair_refused_before_mutations(self):
        with (
            patch.object(t, "walk_tree", return_value=[]),
            self.assertRaisesRegex(ValueError, "empty"),
        ):
            t.stage("1", Path("/source"), Path("/discover"))
        with (
            patch.object(
                t,
                "walk_tree",
                return_value=[
                    {"type": "file", "path": "fixture", "size": 1, "sha256": "0" * 64}
                ],
            ),
            self.assertRaisesRegex(ValueError, "Export"),
        ):
            t.stage("1", Path("/source"), Path("/discover"))
        self.commands.assert_not_called()

    def test_transport_policy_rejects_children_snapshots_and_encryption(self):
        # Run the actual validator rather than the lifecycle fixture's stub.
        original = self.contexts.enter_context(
            patch.object(t, "validate_transport", wraps=self.real_validate_transport)
        )
        values = dict(t.POLICY)
        with (
            patch.object(t, "prop", side_effect=lambda ds, name: values[name]),
            patch.object(t, "datasets", return_value=[self.transport]),
            patch.object(t, "output", return_value=""),
        ):
            self.assertEqual(original(self.pool), self.transport)
            with patch.object(
                t, "datasets", return_value=[self.transport, self.transport + "/child"]
            ):
                with self.assertRaisesRegex(ValueError, "children"):
                    original(self.pool)
            with patch.object(t, "output", return_value=self.transport + "@snapshot"):
                with self.assertRaisesRegex(ValueError, "snapshots"):
                    original(self.pool)
            values["encryption"] = "aes-256-gcm"
            with self.assertRaisesRegex(ValueError, "encryption=off"):
                original(self.pool)

    real_validate_transport = staticmethod(t.validate_transport)

    def test_cancelled_staging_does_not_import_or_destroy(self):
        self.stage_patches()
        with (
            patch("builtins.input", return_value="no"),
            self.assertRaisesRegex(ValueError, "cancelled"),
        ):
            t.stage("1", Path("/source"), Path("/discover"))
        self.commands.assert_not_called()

    def test_degraded_mirror_is_exported_without_destroying_transport(self):
        self.stage_patches()
        with (
            patch.object(
                t, "mirror_leaves", return_value=[("  ", "1", "OFFLINE", "0", "0", "0")]
            ),
            self.assertRaisesRegex(ValueError, "ONLINE"),
        ):
            t.stage("1", Path("/source"), Path("/discover"))
        self.assertNotIn(
            ("zfs", "destroy", self.transport),
            [c.args for c in self.commands.call_args_list],
        )
        self.assertEqual(self.commands.call_args.args, ("zpool", "export", self.pool))

    def test_failed_transport_unmount_preserves_mountpoint_and_payload(self):
        self.props["mounted"] = "no"
        with tempfile.TemporaryDirectory() as directory:
            mountpoint = Path(directory) / "mounted"
            mountpoint.mkdir()
            payload = mountpoint / "payload"
            payload.write_text("retain")

            def command(*args):
                if args[0] == "umount":
                    raise OSError("busy")

            with (
                patch.object(t.tempfile, "mkdtemp", return_value=str(mountpoint)),
                patch.object(t, "command", side_effect=command),
                self.assertRaisesRegex(OSError, "busy"),
            ):
                with t.mounted_transport(self.pool):
                    pass
            self.assertEqual(payload.read_text(), "retain")
