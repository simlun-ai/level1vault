#!/usr/bin/env python3
"""Destructive ONLY to newly created files/pools on a disposable CI VM.

Do not run on a workstation: requires LEVEL1VAULT_DISPOSABLE_TEST_VM=1 and
refuses any pre-existing imported level1vault pool. No physical disk writes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts import Query, RecoveryRecord


import hashlib
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
KEY = "integration-test-passphrase"
PAIRS = ["1", "2", "3", "10"]
POOLS = ["level1-workspace-" + pair for pair in PAIRS]


def run(*args: str | Path) -> str:
    return subprocess.check_output(args, text=True).strip()


def command(
    *args: str | Path,
    stdout: int | None = None,
    input: str | None = None,
    text: bool = False,
) -> None:
    subprocess.run(args, check=True, stdout=stdout, input=input, text=text)


def get(dataset: str, name: str):
    return run("zfs", "get", "-H", "-p", "-o", "value", name, dataset)


def helper(name: str, *args: str | Path, input: str | None = None, ok: bool = True):
    r = subprocess.run(
        [str(ROOT / "runtime" / name), *args],
        text=True,
        input=input,
        capture_output=True,
    )
    if ok:
        assert r.returncode == 0, r.stdout + r.stderr
    else:
        assert r.returncode != 0, r.stdout + r.stderr
    return r


def prepare(base: Path, pair: str, active: bool = False):
    out = base / "pairs" / pair
    out.mkdir(parents=True)
    images = {}
    for side in ["a", "b"]:
        image = out / f"usb-{side}.img"
        command("truncate", "-s", "512M", str(image))
        command(
            "sgdisk",
            "--clear",
            "-n",
            "1:2048:+64M",
            "-t",
            "1:ef00",
            "-n",
            "2:0:0",
            "-t",
            "2:bf01",
            str(image),
            stdout=subprocess.DEVNULL,
        )
        part_uuid = next(
            line.split(": ", 1)[1].strip().lower()
            for line in run("sgdisk", "-i", "2", str(image)).splitlines()
            if line.startswith("Partition unique GUID:")
        )
        images[side] = {"file": image.name, "zfs_partuuid": part_uuid}
    (out / "images.json").write_text(json.dumps({"format": 1, "images": images}))
    (out / "IMAGES-PREPARED").touch()
    cmd = [
        "bash",
        str(ROOT / "stages/03-initialize-pair/initialize.sh"),
        str(out),
        pair,
    ]
    if active:
        cmd.append("--active")
    command(*cmd, input=KEY + "\n", text=True)
    assert (out / "READY").exists()
    return out


def verify_live_home(base: Path, dataset: str, existing: bool = False):
    # Exercise real encrypted bind mounts on test-owned paths, never the runner's home.
    loader = importlib.machinery.SourceFileLoader(
        "workspace_home", str(ROOT / "runtime/workspace-home")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    home = importlib.util.module_from_spec(spec)
    loader.exec_module(home)
    target = base / ("live-home-" + dataset.split("/")[0])
    defaults = base / ("home-defaults-" + dataset.split("/")[0])
    target.mkdir()
    defaults.mkdir()
    (defaults / ".zshrc").write_text("image defaults")
    base.chmod(0o711)
    with patch.object(home, "HOME", target), patch.object(home, "DEFAULTS", defaults):
        try:
            home.attach(dataset)
            assert (
                run("findmnt", "-n", "-o", "FSROOT", "--mountpoint", str(target))
                == "/home/simlun"
            )
            assert (target / ".zshrc").read_text() == (
                "saved configuration" if existing else "image defaults"
            )
            assert (
                target.stat().st_uid,
                target.stat().st_gid,
                target.stat().st_mode & 0o777,
            ) == (1000, 1000, 0o700)
            command(
                "setpriv",
                "--reuid=1000",
                "--regid=1000",
                "--clear-groups",
                "sh",
                "-c",
                'printf "saved configuration" > "$1/.zshrc"',
                "sh",
                str(target),
            )
            busy = subprocess.Popen(
                [
                    "setpriv",
                    "--reuid=1000",
                    "--regid=1000",
                    "--clear-groups",
                    "sh",
                    "-c",
                    "echo ready; exec sleep 30",
                ],
                cwd=target,
                stdout=subprocess.PIPE,
                text=True,
            )
            try:
                assert busy.stdout is not None
                assert busy.stdout.readline().strip() == "ready"
                try:
                    home.detach(dataset.split("/")[0])
                    raise AssertionError("Expected running-user refusal")
                except RuntimeError as error:
                    assert "running processes" in str(error), str(error)
            finally:
                busy.terminate()
                busy.wait()
            home.detach(dataset.split("/")[0])
            home.attach(dataset)
            assert (target / ".zshrc").read_text() == "saved configuration"
            home.check()
        finally:
            if os.path.ismount(target):
                command("umount", str(target))


def verify_os_upgrade(out: Path, base: Path):
    # Update real disposable FAT partitions and verify ZFS bytes remain unchanged.
    sys.path.insert(0, str(ROOT / "stages/06-upgrade-os"))
    sys.path.insert(0, str(ROOT / "stages/06-upgrade-os/tests"))
    import upgrade

    from upgrade_fixtures import make_boot, make_release

    release = base / "upgrade-release"
    manifest = make_release(release)
    (release / "UPGRADE-READY").touch()
    devices: list[str] = []
    try:
        for side in ("a", "b"):
            loop = run(
                "losetup",
                "--find",
                "--show",
                "--partscan",
                str(out / f"usb-{side}.img"),
            )
            devices.append(loop)
            command("udevadm", "settle")
            command("mkfs.vfat", "-F", "32", "-i", os.urandom(4).hex(), loop + "p1")
            esp_uuid = run("blkid", "-p", "-s", "UUID", "-o", "value", loop + "p1")
            with upgrade.mounted(loop + "p1", readonly=False) as root:
                make_boot(root, esp_uuid)

        def edge_hash(loop: str):
            image = Path(
                run("losetup", "--list", "--noheadings", "--output", "BACK-FILE", loop)
            )
            with image.open("rb") as stream:
                first = stream.read(1024 * 1024)
                stream.seek(-1024 * 1024, os.SEEK_END)
                return hashlib.sha256(first + stream.read()).hexdigest()

        edges_before = [edge_hash(loop) for loop in devices]
        data_before = [upgrade.digest(Path(loop + "p2")) for loop in devices]
        table = json.loads(run("sfdisk", "--json", devices[0]))["partitiontable"]
        data_partition = table["partitions"][1]
        sector_size = table.get("sectorsize", 512)
        data_offset = data_partition["start"] * sector_size
        data_size = data_partition["size"] * sector_size
        tables_before = [run("sfdisk", "--dump", loop) for loop in devices]
        describe = upgrade.describe_disk

        def describe_test_loop(path: Path, query: Query):
            # Exercise real layout and label checks on this test's loop devices.
            assert str(path) in devices
            info = describe(path, query)
            assert info["type"] == "loop"
            info.update(type="disk", tran="usb")
            return info

        with patch.object(upgrade, "describe_disk", side_effect=describe_test_loop):
            upgrade.apply_release(release, "1", *map(Path, devices))
            upgrade.apply_release(
                release, "1", *map(Path, devices)
            )  # repeat the same release
        assert [edge_hash(loop) for loop in devices] == edges_before
        assert [upgrade.digest(Path(loop + "p2")) for loop in devices] == data_before
        assert [run("sfdisk", "--dump", loop) for loop in devices] == tables_before
        for loop in devices:
            with upgrade.mounted(loop + "p1", readonly=True) as root:
                upgrade.verify_payload(root, manifest)
    finally:
        for loop in reversed(devices):
            command("losetup", "-d", loop)
    # Exercise the unprivileged QEMU-copy updater on this disposable image too.
    command(
        "python3",
        str(ROOT / "stages/06-upgrade-os/copy_test.py"),
        str(out / "usb-a.img"),
        str(release),
    )
    value = hashlib.sha256()
    with (out / "usb-a.img").open("rb") as stream:
        stream.seek(data_offset)
        remaining = data_size
        while remaining:
            block = stream.read(min(4 * 1024 * 1024, remaining))
            assert block
            value.update(block)
            remaining -= len(block)
    assert value.hexdigest() == data_before[0]


def interrupted_transfer(send: list[str], destination: str, readonly: str):

    # Produce a genuine resumable receive by closing its input mid-stream.
    sender = subprocess.Popen(send, stdout=subprocess.PIPE)
    receiver = subprocess.Popen(
        [
            "zfs",
            "receive",
            "-u",
            "-s",
            "-o",
            "readonly=" + readonly,
            "-o",
            "canmount=noauto",
            "-o",
            "mountpoint=/mnt/workspace",
            "-o",
            "org.openzfs.systemd:ignore=on",
            destination,
        ],
        stdin=subprocess.PIPE,
    )
    assert sender.stdout is not None and receiver.stdin is not None
    try:
        chunk = sender.stdout.read(2 * 1024 * 1024)
        receiver.stdin.write(chunk)
        receiver.stdin.close()
        assert receiver.wait() != 0
    finally:
        sender.stdout.close()
        sender.wait()
    assert get(destination, "receive_resume_token") != "-"
    raise RuntimeError("Injected cable interruption")


def verify_member_replacement(base: Path):
    # Exercise real labels, GUID import, replacement, resilver and final membership.
    # Only USB/live-boot inspection is adapted to these disposable loop devices.
    out = prepare(base, "99", active=True)
    pool = "level1-workspace-99"
    loader = importlib.machinery.SourceFileLoader(
        "integration_replace", str(ROOT / "runtime/workspace-replace")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    replacement = importlib.util.module_from_spec(spec)
    loader.exec_module(replacement)
    import member_recovery
    from contracts import RecoveryRecord

    old = json.loads((out / "images.json").read_text())
    devices: list[str] = []
    image = base / "replacement-a.img"
    command("truncate", "-s", "512M", str(image))
    command(
        "sgdisk",
        "--clear",
        "-n",
        "1:2048:+64M",
        "-t",
        "1:ef00",
        "-n",
        "2:0:0",
        "-t",
        "2:bf01",
        str(image),
        stdout=subprocess.DEVNULL,
    )
    table = member_recovery.partition_table(image)
    info = dict(
        line.split("=", 1)
        for line in (out / "WORKSPACE-INFO.txt").read_text().splitlines()
        if "=" in line
    )
    record = RecoveryRecord(
        format=1,
        pair="99",
        member="a",
        pool_guid=info["pool_guid"],
        failed_partuuid=old["images"]["a"]["zfs_partuuid"],
        survivor_partuuid=old["images"]["b"]["zfs_partuuid"],
        new_partuuid=table["partitions"][1]["uuid"].lower(),
        esp_uuid="ABCD-1234",
        minimum_bytes=table["partitions"][1]["size"] * 512,
    )
    original_read = Path.read_text

    def read_text(path: Path, encoding: str | None = None, errors: str | None = None):
        if str(path) == "/run/live/medium/member-recovery.json":
            return json.dumps(record)
        return original_read(path, encoding=encoding, errors=errors)

    def inspect(
        disk: Path,
        current_record: RecoveryRecord,
        boot: bool = False,
        empty: bool = True,
    ):
        assert current_record == record and boot
        actual = member_recovery.partition_table(disk)
        assert actual["partitions"][1]["uuid"].lower() == record["new_partuuid"]
        if empty:
            member_recovery.empty_partition(actual["partitions"][1]["node"])
        return dict(model="disposable-loop", serial="test"), actual

    try:
        for side in ("a", "b"):
            devices.append(
                run(
                    "losetup",
                    "--find",
                    "--show",
                    "--partscan",
                    str(out / f"usb-{side}.img"),
                )
            )
        command("udevadm", "settle")
        command(
            "zpool",
            "import",
            "-N",
            "-d",
            "/dev/disk/by-partuuid",
            "-o",
            "cachefile=none",
            record["pool_guid"],
        )
        command("zfs", "load-key", pool + "/data", input=KEY + "\n", text=True)
        command("zfs", "mount", pool + "/data")
        Path("/mnt/workspace/replacement-check").write_text(
            "surviving encrypted payload"
        )
        command("zfs", "unmount", pool + "/data")
        command("zpool", "export", pool)
        command("losetup", "-d", devices.pop(0))
        devices.append(run("losetup", "--find", "--show", "--partscan", str(image)))
        command("udevadm", "settle")
        command(
            "mkfs.vfat",
            "-F",
            "32",
            "-i",
            "ABCD1234",
            devices[1] + "p1",
            stdout=subprocess.DEVNULL,
        )
        command(
            "udevadm",
            "trigger",
            "--action=change",
            "--sysname-match=" + Path(devices[1] + "p1").name,
        )
        command("udevadm", "settle")
        with (
            patch.object(Path, "read_text", read_text),
            patch.object(replacement, "inspect_replacement", side_effect=inspect),
        ):
            replacement.replace("99", True)
            replacement.replace("99", True)  # a completed recovery can be checked again
        assert (
            run("zpool", "get", "-H", "-o", "value", "guid", pool)
            == record["pool_guid"]
        )
        assert get(pool + "/data", "encryption") == "aes-256-gcm"
        assert get(pool, "org.level1vault:state") == "active"
        command("zfs", "load-key", pool + "/data", input=KEY + "\n", text=True)
        command("zfs", "mount", pool + "/data")
        assert (
            Path("/mnt/workspace/replacement-check").read_text()
            == "surviving encrypted payload"
        )
        command("zfs", "unmount", pool + "/data")
        command("zpool", "export", pool)
        print("Real single-member replacement and completed-recovery retry passed.")
    finally:
        if pool in run("zpool", "list", "-H", "-o", "name").splitlines():
            command("zpool", "export", pool)
        for device in devices:
            command("losetup", "-d", device)


def verify_transport(base: Path):
    # Stage and replace packages with keys unloaded, then verify real encrypted inbox IO.
    source = base / "transport-source"
    source.mkdir()
    (source / "README.md").write_text("transfer instructions")
    (source / ".hidden").write_bytes(b"payload\x00")
    (source / "empty").mkdir()
    (source / "link").symlink_to("/etc/shadow")
    pool = "level1-workspace-1"
    command("zpool", "export", pool)
    stage = [
        "python3",
        str(ROOT / "pipeline/stage-transport.py"),
        "--pair",
        "1",
        "--source",
        str(source),
    ]
    command(*stage, input="yes\n", text=True)
    assert pool not in run("zpool", "list", "-H", "-o", "name").splitlines()
    (source / "README.md").write_text("replacement instructions")
    command(*stage, input="yes\n", text=True)
    command(
        "zpool",
        "import",
        "-N",
        "-d",
        "/dev/disk/by-partuuid",
        "-o",
        "cachefile=none",
        pool,
    )
    assert get(pool + "/data", "keystatus") == "unavailable"
    assert get(pool + "/data", "mounted") == "no"
    assert get(pool + "/transport", "encryption") == "off"
    helper("workspace-mount", "1", input=KEY + "\n")

    # A corrupt delivery must survive a refused import; restaging repairs it.
    transport_mount = base / "transport-mounted"
    transport_mount.mkdir()
    command("mount", "-t", "zfs", pool + "/transport", str(transport_mount))
    (transport_mount / "files/README.md").write_text("corrupted")
    command("umount", str(transport_mount))
    helper("workspace-transport-import", "1", ok=False)
    assert get(pool + "/transport", "encryption") == "off"
    helper("workspace-umount", "1")
    command(*stage, input="yes\n", text=True)
    helper("workspace-mount", "1", input=KEY + "\n")
    helper("workspace-transport-import", "1")
    deliveries = list(Path("/mnt/workspace/inbox").glob("[0-9]*"))
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert (delivery / "README.md").read_text() == "replacement instructions"
    assert (delivery / ".hidden").read_bytes() == b"payload\x00"
    assert (delivery / "empty").is_dir()
    assert (delivery / "link").readlink() == Path("/etc/shadow")
    for path in [delivery, delivery / "README.md", delivery / "link"]:
        info = path.lstat()
        assert (info.st_uid, info.st_gid) == (1000, 1000)
    assert (
        pool + "/transport" not in run("zfs", "list", "-H", "-o", "name").splitlines()
    )
    command("zfs", "unmount", pool + "/data")
    command("zfs", "unload-key", pool + "/data")

    # Transport on a replica never changes its role, and may coexist with later raw receives.
    command("zpool", "export", "level1-workspace-2")
    stage[stage.index("--pair") + 1] = "2"
    command(*stage, input="yes\n", text=True)
    helper("workspace-mount", "2", input=KEY + "\n")
    helper("workspace-transport-import", "2", ok=False)
    assert get("level1-workspace-2", "org.level1vault:state") == "replica"
    command("zfs", "unmount", "level1-workspace-2/data")
    command("zfs", "unload-key", "level1-workspace-2/data")


def main():
    assert os.geteuid() == 0 and os.environ.get("LEVEL1VAULT_DISPOSABLE_TEST_VM") == "1"
    command("modprobe", "zfs")
    assert not set(run("zpool", "list", "-H", "-o", "name").splitlines()).intersection(
        POOLS
    )
    assert not os.path.ismount("/mnt/workspace")

    # Load replication helpers for interrupted-transfer tests.
    loader = importlib.machinery.SourceFileLoader(
        "workspace_sync", str(ROOT / "runtime/workspace-sync")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    sync = importlib.util.module_from_spec(spec)
    loader.exec_module(sync)

    # Track test-owned resources for cleanup.
    loops: list[str] = []
    owned: list[str] = []
    with tempfile.TemporaryDirectory(prefix="level1vault-zfs-") as tmp:
        base = Path(tmp)
        try:
            for pair, pool in zip(PAIRS, POOLS, strict=False):
                prepare(base, pair, active=(pair == "1"))
                owned.append(pool)

            verify_os_upgrade(base / "pairs/1", base)
            verify_member_replacement(base)

            # Verify pre-flash replica seeding and completion markers.
            command(
                "python3",
                str(ROOT / "stages/03-initialize-pair/seed.py"),
                str(base),
                "2",
            )
            assert not set(
                run("zpool", "list", "-H", "-o", "name").splitlines()
            ).intersection(POOLS)
            for pair in ["1", "2"]:
                assert (base / "pairs" / pair / "READY").is_file()
            for pair in PAIRS:
                pool = "level1-workspace-" + pair
                out = base / "pairs" / pair
                for side in ["a", "b"]:
                    loops.append(
                        run(
                            "losetup",
                            "--find",
                            "--show",
                            "--partscan",
                            str(out / f"usb-{side}.img"),
                        )
                    )
                    if pair == "1" and side == "a":
                        command("udevadm", "settle")
                        helper(
                            "workspace-mount", ok=False
                        )  # one member is insufficient for automatic selection
                        assert (
                            pool
                            not in run("zpool", "list", "-H", "-o", "name").splitlines()
                        )
                command("udevadm", "settle")
                if pair == "1":
                    # Exercise real offline label discovery and mounting before any other pair is attached.
                    assert helper("workspace-discover").stdout.strip() == "1"
                    helper("workspace-mount", input=KEY + "\n")
                    assert get("level1-workspace-1/data", "mounted") == "yes"
                    assert (
                        helper("workspace-discover").stdout.strip() == "1"
                    )  # already imported
                    command("zfs", "unmount", "level1-workspace-1/data")
                    command("zfs", "unload-key", "level1-workspace-1/data")
                else:
                    helper(
                        "workspace-mount", ok=False
                    )  # several complete pairs require a number
                    assert not os.path.ismount("/mnt/workspace")
                    command(
                        "zpool",
                        "import",
                        "-N",
                        "-d",
                        "/dev",
                        "-o",
                        "cachefile=none",
                        pool,
                    )

            verify_transport(base)

            # Verify passphrase unlock and workspace ownership.
            helper("workspace-mount", "1", input="incorrect-passphrase\n", ok=False)
            assert not os.path.ismount("/mnt/workspace")
            helper("workspace-mount", "1", input=KEY + "\n")
            verify_live_home(base, "level1-workspace-1/data")
            stat = os.stat("/mnt/workspace")
            assert (stat.st_uid, stat.st_gid, stat.st_mode & 0o777) == (
                1000,
                1000,
                0o750,
            )
            command(
                "setpriv",
                "--reuid=1000",
                "--regid=1000",
                "--clear-groups",
                "sh",
                "-c",
                "printf first > /mnt/workspace/user-file",
            )
            command("zfs", "unmount", "level1-workspace-1/data")
            command("zfs", "unload-key", "level1-workspace-1/data")

            # Verify full and incremental encrypted replication.
            helper(
                "workspace-sync", "1", "2", "10"
            )  # shared full checkpoint, source key unloaded
            assert get("level1-workspace-2/data", "readonly") == "on"
            assert get("level1-workspace-2/data", "keystatus") == "unavailable"
            assert get("level1-workspace-2/transport", "encryption") == "off"
            helper("workspace-mount", "1", input=KEY + "\n")
            Path("/mnt/workspace/user-file").write_text("second")
            helper("workspace-sync", "1", "2")
            with open("/mnt/workspace/random", "wb") as f:
                f.write(os.urandom(32 * 1024 * 1024))
            real_transfer = sync.transfer

            # A new pair's full raw receive is interrupted and resumed.
            vars(sync)["transfer"] = interrupted_transfer
            try:
                sync.synchronize("1", "3")
                raise AssertionError("Expected injected interruption")
            except RuntimeError as e:
                assert str(e) == "Injected cable interruption", str(e)
            finally:
                vars(sync)["transfer"] = real_transfer
            helper("workspace-sync", "1", "3")
            assert get("level1-workspace-3/data", "receive_resume_token") == "-"

            # A later incremental receive resumes independently.
            vars(sync)["transfer"] = interrupted_transfer
            try:
                sync.synchronize("1", "2")
                raise AssertionError("Expected injected interruption")
            except RuntimeError as e:
                assert str(e) == "Injected cable interruption", str(e)
            finally:
                vars(sync)["transfer"] = real_transfer
            helper("workspace-sync", "1", "2")

            # Reject child datasets and verify a shared checkpoint across replicas.
            command(
                "zfs",
                "create",
                "-u",
                "-o",
                "canmount=off",
                "-o",
                "mountpoint=none",
                "level1-workspace-1/data/child",
            )
            helper("workspace-sync", "1", "2", ok=False)
            command("zfs", "destroy", "level1-workspace-1/data/child")
            Path("/mnt/workspace/user-file").write_text("third")
            helper("workspace-sync", "1", "2", "3", "10")
            checkpoint = sync.snapshots("level1-workspace-1/data")[-1]
            for pair in PAIRS[1:]:
                latest = sync.snapshots("level1-workspace-" + pair + "/data")[-1]
                assert latest[1] == checkpoint[1], (pair, latest, checkpoint)
            held = run(
                "zfs",
                "holds",
                "-H",
                "-r",
                *[name for name, _ in sync.snapshots("level1-workspace-1/data")],
            )
            for pool in POOLS[1:]:
                assert (
                    "level1vault-"
                    + run("zpool", "get", "-H", "-o", "value", "guid", pool)
                    in held
                )

            # Retention preserves held replication bases and user snapshots.
            source = "level1-workspace-1/data"
            retained = sync.create_checkpoint(source)
            command("zfs", "hold", "operator-hold", retained)
            obsolete = sync.create_checkpoint(source)
            newest = sync.create_checkpoint(source)
            user_snapshot = source + "@operator-checkpoint"
            command("zfs", "snapshot", user_snapshot)
            before = sync.snapshots(source)
            helper("workspace-prune", "1", "--keep", "1")
            assert sync.snapshots(source) == before
            helper("workspace-prune", "1", "--keep", "1", "--apply")
            after = dict(sync.snapshots(source))
            assert obsolete not in after
            assert all(
                name in after
                for name in (retained, newest, user_snapshot, checkpoint[0])
            )
            helper("workspace-prune", "2", "--keep", "1", "--apply")
            assert sync.snapshots("level1-workspace-2/data")[-1][1] == checkpoint[1]
            helper("workspace-sync", "1", "2", "3", "10")
            checkpoint = sync.snapshots(source)[-1]
            for pair in PAIRS[1:]:
                assert (
                    sync.snapshots("level1-workspace-" + pair + "/data")[-1][1]
                    == checkpoint[1]
                )

            # Reusing an explicit checkpoint when rotating pairs separately.
            helper(
                "workspace-sync", "--snapshot", checkpoint[0].split("@")[1], "1", "10"
            )
            helper("workspace-sync", "2", "3", ok=False)  # inactive source
            helper("workspace-activate", "2", ok=False)  # no fencing acknowledgement
            helper(
                "workspace-activate", "--next", "1", "--fenced", ok=False
            )  # old active still imported
            Path("/mnt/workspace/former-only").write_text("preserve on fenced owner")
            command("zfs", "unmount", "level1-workspace-1/data")
            helper("workspace-mount", "2", input=KEY + "\n")
            assert Path("/mnt/workspace/user-file").read_text() == "third"
            assert (
                subprocess.run(["touch", "/mnt/workspace/should-fail"]).returncode != 0
            )
            command("zfs", "unmount", "level1-workspace-2/data")

            # Fence the former active owner. Names/identities are never changed.
            command("zpool", "export", "level1-workspace-1")
            helper("workspace-activate", "--next", "1", "--fenced")
            assert get("level1-workspace-2", "org.level1vault:state") == "active"
            assert get("level1-workspace-10", "org.level1vault:state") == "replica"
            helper("workspace-activate", "2", "--fenced")  # idempotent
            helper("workspace-mount", "2", input=KEY + "\n")
            command(
                "setpriv",
                "--reuid=1000",
                "--regid=1000",
                "--clear-groups",
                "sh",
                "-c",
                "printf fourth > /mnt/workspace/user-file",
            )

            verify_live_home(base, "level1-workspace-2/data", existing=True)

            # Binding changes are explicit; verified common history allows
            # incrementals from the newly active pair without destroying replicas.
            helper("workspace-sync", "2", "3", ok=False)
            dataset_guid = get("level1-workspace-3/data", "guid")
            helper("workspace-sync", "--rebind", "2", "3", "10")
            assert get("level1-workspace-3/data", "guid") == dataset_guid
            assert get("level1-workspace-3", "org.level1vault:source-pool-guid") == run(
                "zpool", "get", "-H", "-o", "value", "guid", "level1-workspace-2"
            )
            command("zfs", "unmount", "level1-workspace-2/data")
            command("zpool", "export", "level1-workspace-2")

            # A second ordered failover selects 3 rather than lexicographic 10.
            helper("workspace-activate", "--next", "2", "--fenced")
            assert get("level1-workspace-3", "org.level1vault:state") == "active"
            helper("workspace-mount", "3", input=KEY + "\n")
            assert Path("/mnt/workspace/user-file").read_text() == "fourth"
            assert os.stat("/mnt/workspace").st_uid == 1000
            Path("/mnt/workspace/user-file").write_text("fifth")
            helper("workspace-sync", "--rebind", "3", "10")

            # A mounted destination or divergent local snapshot is never forced.
            command("zfs", "unmount", "level1-workspace-3/data")
            helper("workspace-mount", "10", input=KEY + "\n")
            helper("workspace-sync", "3", "10", ok=False)
            command("zfs", "unmount", "level1-workspace-10/data")
            command("zfs", "snapshot", "level1-workspace-10/data@unexpected-local")
            helper("workspace-sync", "3", "10", ok=False)
            command("zfs", "destroy", "level1-workspace-10/data@unexpected-local")
            helper("workspace-sync", "3", "10")

            # A returning former owner can contain newer unsnapshotted writes.
            # Even after manual demotion those must never be overwritten.
            command(
                "zpool",
                "import",
                "-N",
                "-d",
                "/dev",
                "-o",
                "cachefile=none",
                "level1-workspace-1",
            )
            helper("workspace-mount", "3", ok=False)  # two observable active owners
            command("zfs", "set", "readonly=on", "level1-workspace-1/data")
            command("zfs", "set", "org.level1vault:state=replica", "level1-workspace-1")
            old_guid = get("level1-workspace-1/data", "guid")
            helper("workspace-sync", "--rebind", "3", "1", ok=False)
            assert get("level1-workspace-1/data", "guid") == old_guid
            helper("workspace-mount", "1", input=KEY + "\n")
            assert Path("/mnt/workspace/user-file").read_text() == "third"
            assert (
                Path("/mnt/workspace/former-only").read_text()
                == "preserve on fenced owner"
            )

            # Verify all workspace pairs export cleanly.
            helper("workspace-umount")
            assert not set(
                run("zpool", "list", "-H", "-o", "name").splitlines()
            ).intersection(POOLS)
            print(
                "Real encrypted n-pair sync, resume, shared checkpoints and ordered in-place failovers passed."
            )
        finally:
            # This VM is disposable; destroy only pools this test created. If
            # export fails, keep files/loops and fail rather than force detaching.
            for pool in owned:
                if pool in run("zpool", "list", "-H", "-o", "name").splitlines():
                    command("zpool", "export", pool)
            for loop in loops:
                command("losetup", "-d", loop)


if __name__ == "__main__":
    main()
