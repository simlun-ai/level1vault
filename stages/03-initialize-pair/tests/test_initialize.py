"""Host initializer command-flow tests; kernel/ZFS commands are mocked."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class InitializeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.out = self.base / "out"
        self.out.mkdir()
        self.log = self.base / "calls"
        self.env = dict(
            os.environ,
            PATH=f"{self.bin}:{os.environ['PATH']}",
            LOG=str(self.log),
            STATE=str(self.base / "pool"),
            MOCK=str(self.base),
            TMPDIR=str(self.base),
        )
        self.tool("modprobe", ":")
        self.tool("udevadm", ":")
        self.tool(
            "zpool",
            """
case "$1" in
list) if [ "$*" = 'list -H' ]; then exit 0; fi; test -e "$STATE";;
create) touch "$STATE";;
export) if [ "${FAIL_EXPORT:-0}" = 1 ]; then exit 1; fi; rm -f "$STATE";;
get) echo 123456;;
--version) printf 'zfs-2.1.5-ubuntu\nzfs-kmod-2.1.5-ubuntu-kernel\n';;
esac""",
        )
        self.tool("zfs", ":")
        self.tool("chown", ":")
        self.tool("chmod", ":")
        self.tool(
            "wipefs", 'if [ "${OLD_SIGNATURE:-0}" = 1 ]; then echo zfs_member; fi'
        )
        self.tool(
            "blkid", 'case "$*" in *loop-ap2) echo aaa;; *loop-bp2) echo bbb;; esac'
        )
        self.tool(
            "losetup",
            """case "$1" in
--find) case "$*" in *usb-a.img) echo "$MOCK/loop-a";; *usb-b.img) echo "$MOCK/loop-b";; esac;;
esac""",
        )
        for name in ("a", "b"):
            (self.base / f"loop-{name}p2").touch()
            (self.out / f"usb-{name}.img").write_text(f"empty-{name}")
        (self.out / "IMAGES-PREPARED").touch()
        (self.out / "images.json").write_text(
            json.dumps(
                {
                    "format": 1,
                    "images": {
                        "a": {"file": "usb-a.img", "zfs_partuuid": "aaa"},
                        "b": {"file": "usb-b.img", "zfs_partuuid": "bbb"},
                    },
                }
            )
        )
        # These tests do no privileged IO: replace the root guard, lock location
        # and block-node predicate, while retaining the remaining script logic.
        text = (ROOT / "stages/03-initialize-pair/initialize.sh").read_text()
        text = text.replace(
            '$(dirname -- "${BASH_SOURCE[0]}")/../../common/artifact_owner.py',
            str(ROOT / "common/artifact_owner.py"),
        )
        text = (
            "\n".join(
                line for line in text.splitlines() if not line.startswith("[[ $EUID")
            )
            + "\n"
        )
        text = text.replace(
            "/run/lock/level1vault-initialize.lock", str(self.base / "lock")
        )
        text = text.replace("[[ -b ${dev}p2 ]]", "[[ -e ${dev}p2 ]]")
        self.script = self.base / "initialize.sh"
        self.script.write_text(text)

    def tool(self, name, body):
        p = self.bin / name
        p.write_text(f'#!/bin/sh\nset -eu\necho "{name} $*" >> "$LOG"\n' + body + "\n")
        p.chmod(0o755)

    def run_initialize(self, *args):
        return subprocess.run(
            ["bash", str(self.script), str(self.out), *(args or ("1", "--active"))],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def calls(self):
        return self.log.read_text() if self.log.exists() else ""

    def test_ubuntu_versions_and_conservative_pool_creation(self):
        r = self.run_initialize()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("zpool create -d -R ", self.calls())
        self.assertIn(
            "-o feature@lz4_compress=enabled -o feature@encryption=enabled",
            self.calls(),
        )
        self.assertIn(
            "-o encryption=aes-256-gcm -o keyformat=passphrase -o keylocation=prompt",
            self.calls(),
        )
        self.assertIn("chown 1000:1000", self.calls())
        self.assertIn("-o canmount=noauto", self.calls())
        self.assertEqual(self.calls().count("zpool export level1-workspace-1"), 1)
        self.assertEqual(self.calls().count("losetup -d"), 2)
        self.assertTrue((self.out / "READY").exists())
        self.assertFalse((self.out / "SHA256SUMS").exists())
        self.assertFalse((self.out / "INITIALIZING").exists())
        self.assertNotIn("zpool import", self.calls())
        self.assertNotIn("zpool upgrade", self.calls())

    def test_existing_host_pool_is_untouched(self):
        (self.base / "pool").touch()
        r = self.run_initialize()
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("zpool export", self.calls())
        self.assertNotIn("zpool create", self.calls())
        self.assertNotIn("losetup --find", self.calls())

    def test_missing_prepared_marker_precedes_host_mutations(self):
        (self.out / "IMAGES-PREPARED").unlink()
        r = self.run_initialize()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.calls(), "")

    def test_existing_member_signature_is_rejected(self):
        self.env["OLD_SIGNATURE"] = "1"
        r = self.run_initialize()
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("zpool create", self.calls())
        self.assertEqual(self.calls().count("losetup -d"), 2)
        self.assertFalse((self.out / "READY").exists())

    def test_export_failure_retains_loops_and_failure_marker(self):
        self.env["FAIL_EXPORT"] = "1"
        r = self.run_initialize()
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("losetup -d", self.calls())
        self.assertTrue((self.out / "INITIALIZING").exists())
        self.assertFalse((self.out / "READY").exists())

    def test_second_initialization_refused(self):
        self.assertEqual(self.run_initialize().returncode, 0)
        self.log.write_text("")
        self.assertNotEqual(self.run_initialize().returncode, 0)
        self.assertEqual(self.calls(), "")

    def test_arbitrary_numbered_pairs_are_empty_receive_targets(self):
        r = self.run_initialize("27")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(
            "org.level1vault:pair=27 -O org.level1vault:state=replica level1-workspace-27 mirror",
            self.calls(),
        )
        self.assertNotIn("zfs create", self.calls())
        self.assertNotIn("zpool export level1-workspace-1\n", self.calls())

    def test_default_pair_has_no_independently_encrypted_data(self):
        r = self.run_initialize("2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("zfs create", self.calls())

    def test_bad_pair_rejected_before_changes(self):
        self.assertNotEqual(self.run_initialize("backup3").returncode, 0)
        self.assertEqual(self.calls(), "")
