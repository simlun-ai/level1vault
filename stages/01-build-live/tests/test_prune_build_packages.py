"""Prevent pre-existing toolchains from escaping final-image cleanup."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

STAGE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "prune_build_packages", STAGE / "prune-build-packages.py"
)
assert spec is not None and spec.loader is not None
pruner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pruner)


class BuildCleanupTests(unittest.TestCase):
    def test_preexisting_compilers_and_headers_are_included_in_removal(self):
        # Cleanup must consider the whole image, not just newly installed packages.
        baseline = [
            "gcc",
            "gcc-14",
            "gcc-14-x86-64-linux-gnu",
            "cpp-14",
            "dpkg-dev",
            "libc6-dev:amd64",
            "linux-libc-dev",
        ]
        added = [
            "g++-14",
            "linux-headers-6.12-test-amd64",
            "linux-kbuild-6.12-test",
            "zfs-dkms",
            "dkms",
        ]
        inventory = "".join(f"{name}\tinstalled\n" for name in baseline + added)
        self.assertEqual(pruner.removal_plan(inventory), sorted(baseline + added))

    def test_runtime_libraries_kernel_and_standalone_modules_are_retained(self):
        runtime = [
            "gcc-14-base:amd64",
            "libgcc-s1:amd64",
            "libstdc++6:amd64",
            "linux-image-6.12-test-amd64",
            "level1vault-zfs-modules",
            "zfsutils-linux",
            "zfs-zed",
            "python3",
            "python3-venv",
            "make",
            "kmod",
            "initramfs-tools",
        ]
        self.assertEqual(
            pruner.removal_plan("".join(f"{name}\tinstalled\n" for name in runtime)), []
        )

    def test_uninstalled_packages_are_ignored_and_multiarch_names_preserved(self):
        inventory = "gcc\tconfig-files\nlibcrypt-dev:amd64\tinstalled\nlibcrypt-dev:i386\tnot-installed\n"
        self.assertEqual(pruner.removal_plan(inventory), ["libcrypt-dev:amd64"])
