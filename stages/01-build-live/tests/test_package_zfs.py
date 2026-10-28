"""Verify the real standalone Debian module package without loading kernel code."""

import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

STAGE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_zfs", STAGE / "package-zfs.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


@unittest.skipUnless(shutil.which("dpkg-deb"), "Debian package tools required")
class ModulePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = patch.dict(os.environ, TMPDIR=str(self.root))
        environment.start()
        self.addCleanup(environment.stop)
        self.modules = self.root / "dkms"
        self.modules.mkdir()
        self.copyright = self.root / "copyright"
        self.copyright.write_text("Debian OpenZFS license fixture\n")
        self.output = self.root / "modules.deb"

    def build(self):
        packager.build_package(
            "6.12-test-amd64",
            "2.3.9-1",
            "amd64",
            self.modules,
            self.copyright,
            self.output,
        )

    def test_package_preserves_modules_after_dkms_files_disappear(self):
        # Package plain and compressed modules, excluding unrelated DKMS metadata.
        (self.modules / "zfs.ko").write_bytes(b"test zfs module")
        (self.modules / "spl.ko.zst").write_bytes(b"test dependency")
        (self.modules / "build.log").write_text("not shipped")
        self.build()
        shutil.rmtree(self.modules)
        target = self.root / "unpacked"
        subprocess.run(["dpkg-deb", "-x", str(self.output), str(target)], check=True)
        modules = target / "usr/lib/modules/6.12-test-amd64/extra/level1vault"
        self.assertEqual((modules / "zfs.ko").read_bytes(), b"test zfs module")
        self.assertEqual(
            sorted(p.name for p in modules.iterdir()), ["spl.ko.zst", "zfs.ko"]
        )
        self.assertEqual(
            (target / "usr/share/doc/level1vault-zfs-modules/copyright").read_bytes(),
            self.copyright.read_bytes(),
        )

        # Bind the module package to the exact kernel and ZFS userland.
        control = subprocess.check_output(
            ["dpkg-deb", "-f", str(self.output)], text=True
        )
        self.assertIn(
            "Depends: linux-image-6.12-test-amd64, zfsutils-linux (= 2.3.9-1), kmod",
            control,
        )
        self.assertIn("Provides: zfs-modules (= 2.3.9-1)", control)
        self.assertNotIn("zfs-dkms", control)

    def test_missing_zfs_module_and_symlink_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            self.build()
        (self.modules / "zfs.ko").symlink_to(self.copyright)
        with self.assertRaisesRegex(ValueError, "regular"):
            self.build()

    def test_control_metadata_cannot_add_fields(self):
        with self.assertRaisesRegex(ValueError, "metadata"):
            packager.build_package(
                "bad\nDepends: other",
                "1",
                "amd64",
                self.modules,
                self.copyright,
                self.output,
            )
