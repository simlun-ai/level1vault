"""Offline transport packaging, verification and ZFS lifecycle shared by host and live OS."""

import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from member_recovery import command, mirror_leaves, output, require

POLICY = {
    "encryption": "off",
    "canmount": "noauto",
    "mountpoint": "legacy",
    "exec": "off",
    "setuid": "off",
    "devices": "off",
    "org.openzfs.systemd:ignore": "on",
}
DELIVERY = r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}"


def prop(dataset, name):
    return output("zfs", "get", "-H", "-p", "-o", "value", name, dataset)


def pair_pool(pair):
    require(re.fullmatch(r"[1-9][0-9]*", pair) is not None, "Invalid pair ID")
    return "level1-workspace-" + pair


@contextmanager
def workspace_lock():
    require(os.geteuid() == 0, "Run with sudo")
    with open("/run/lock/level1vault-workspace.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        command("modprobe", "zfs")
        if not Path("/etc/hostid").exists() or not Path("/etc/hostid").stat().st_size:
            command("zgenhostid")
        command("udevadm", "settle")
        yield


def datasets(pool):
    return output(
        "zfs", "list", "-H", "-o", "name", "-t", "filesystem,volume", "-r", pool
    ).splitlines()


def validate_transport(pool):
    # A narrowly defined sibling may coexist with encrypted workspace replication.
    transport = pool + "/transport"
    for name, expected in POLICY.items():
        require(
            prop(transport, name) == expected,
            f"{transport}: expected {name}={expected}",
        )
    require(
        datasets(transport) == [transport], "Unexpected transport children; refusing"
    )
    require(
        not output(
            "zfs", "list", "-H", "-t", "snapshot", "-o", "name", "-r", transport
        ),
        "Transport snapshots are unsupported; inspect before deleting",
    )
    return transport


def validate_pair(pool, pair):
    data = pool + "/data"
    require(prop(pool, "org.level1vault:pair") == pair, "Pair identity mismatch")
    require(
        set(datasets(pool)) <= {pool, data, pool + "/transport"},
        "Unexpected pool datasets",
    )
    require(
        prop(data, "encryption") == "aes-256-gcm"
        and prop(data, "encryptionroot") == data
        and prop(data, "keyformat") == "passphrase",
        "Expected a passphrase-encrypted workspace",
    )
    require(
        (prop(pool, "org.level1vault:state"), prop(data, "readonly"))
        in (("active", "off"), ("replica", "on")),
        "Pair state and readonly policy disagree",
    )
    return data


@contextmanager
def mounted_transport(pool, readonly=False):
    transport = validate_transport(pool)
    require(
        prop(transport, "mounted") == "no", "Unmount transport before this operation"
    )
    directory = Path(tempfile.mkdtemp(prefix="level1vault-transport-"))
    mounted = False
    try:
        command(
            "mount",
            "-t",
            "zfs",
            "-o",
            ("ro" if readonly else "rw") + ",nodev,nosuid,noexec",
            transport,
            directory,
        )
        mounted = True
        yield directory
    finally:
        # Preserve the mountpoint on failure; recursive temp cleanup could delete the payload.
        if mounted:
            command("umount", directory)
        directory.rmdir()


def regular_bytes(path, limit=64 * 1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        require(
            stat.S_ISREG(os.fstat(source.fileno()).st_mode),
            f"Expected regular file: {path}",
        )
        data = source.read(limit + 1)
        require(len(data) <= limit, f"Metadata too large: {path}")
        return data


def walk_tree(source, destination=None):
    # Directory-relative, no-follow opens keep symlinks inert, including during source changes.
    entries = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(source, flags)
    device = os.fstat(root_fd).st_dev

    def walk(fd, prefix, target):
        for name in sorted(os.listdir(fd)):
            path = prefix + name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(info.st_dev == device, f"Nested mounts are unsupported: {path}")
            mode = stat.S_IMODE(info.st_mode) & 0o777
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, flags, dir_fd=fd)
                try:
                    if target is not None:
                        (target / name).mkdir(mode=0o700)
                    entries.append({"path": path, "type": "directory"})
                    walk(child, path + "/", None if target is None else target / name)
                finally:
                    os.close(child)
            elif stat.S_ISLNK(info.st_mode):
                link = os.readlink(name, dir_fd=fd)
                entries.append({"path": path, "type": "symlink", "target": link})
                if target is not None:
                    (target / name).symlink_to(link)
            else:
                require(
                    stat.S_ISREG(info.st_mode), f"Special files are unsupported: {path}"
                )
                file_fd = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
                )
                digest, size = hashlib.sha256(), 0
                with os.fdopen(file_fd, "rb") as stream:
                    require(
                        stat.S_ISREG(os.fstat(stream.fileno()).st_mode),
                        f"File changed: {path}",
                    )
                    sink = (target / name).open("xb") if target is not None else None
                    try:
                        while block := stream.read(1024 * 1024):
                            digest.update(block)
                            size += len(block)
                            if sink is not None:
                                sink.write(block)
                        if sink is not None:
                            sink.flush()
                            os.fchmod(sink.fileno(), mode | 0o600)
                            os.fsync(sink.fileno())
                    finally:
                        if sink is not None:
                            sink.close()
                entries.append(
                    {
                        "path": path,
                        "type": "file",
                        "size": size,
                        "sha256": digest.hexdigest(),
                    }
                )
        if target is not None:
            sync_directory(target)

    try:
        walk(root_fd, "", destination)
    finally:
        os.close(root_fd)
    return entries


def canonical(manifest):
    return (json.dumps(manifest, sort_keys=True, ensure_ascii=True) + "\n").encode(
        "ascii"
    )


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_metadata(path, data):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def package(source, destination, entries):
    # Hash the source before copying, then verify the staged bytes before declaring completion.
    files = destination / "files"
    files.mkdir(mode=0o700)
    require(
        walk_tree(source, files) == entries and walk_tree(files) == entries,
        "Source changed or staged copy differs; rerun staging",
    )
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest = {
        "format": 1,
        "delivery": stamp + "-" + uuid.uuid4().hex,
        "entries": entries,
    }
    raw = canonical(manifest)
    write_metadata(destination / "manifest.json", raw)
    write_metadata(
        destination / "READY", hashlib.sha256(raw).hexdigest().encode("ascii")
    )
    sync_directory(destination)
    return manifest


def read_package(source):
    require(
        set(os.listdir(source)) == {"files", "manifest.json", "READY"},
        "Incomplete or unexpected transport package",
    )
    raw = regular_bytes(source / "manifest.json")
    require(
        regular_bytes(source / "READY", 64)
        == hashlib.sha256(raw).hexdigest().encode("ascii"),
        "Transport manifest checksum mismatch",
    )
    manifest = json.loads(raw)
    require(isinstance(manifest, dict), "Invalid transport manifest")
    require(
        manifest.get("format") == 1
        and isinstance(manifest.get("delivery"), str)
        and re.fullmatch(DELIVERY, manifest["delivery"]) is not None,
        "Invalid transport manifest",
    )
    # Comparing with an actual inventory also rejects traversal paths, duplicates and unsupported types.
    require(
        walk_tree(source / "files") == manifest.get("entries"),
        "Transport file verification failed",
    )
    return manifest


def private_directory(path, owner=0):
    if not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o700)
    info = path.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == owner
        and stat.S_IMODE(info.st_mode) == 0o700,
        f"Unexpected directory ownership or permissions: {path}",
    )


def publish_inbox(source, workspace, pool):
    # Keep receipts separate from user payloads; verify an existing delivery before retrying cleanup.
    manifest = read_package(source)
    inbox = workspace / "inbox"
    if not inbox.exists() and not inbox.is_symlink():
        inbox.mkdir(mode=0o700)
        os.chown(inbox, 1000, 1000)
    private_directory(inbox, 1000)
    require(
        inbox.stat().st_dev == workspace.stat().st_dev,
        "Inbox must be inside the encrypted data filesystem",
    )
    receipts = inbox / ".transport-receipts"
    private_directory(receipts)
    delivery = manifest["delivery"]
    final = inbox / delivery
    receipt = receipts / (delivery + ".json")
    raw = canonical(manifest)
    if receipt.exists() or receipt.is_symlink():
        require(
            regular_bytes(receipt) == raw,
            "Delivery ID already has a different manifest",
        )
    if final.exists() or final.is_symlink():
        require(
            receipt.exists() and walk_tree(final) == manifest["entries"],
            "Existing inbox delivery differs; transport retained",
        )
        os.chown(final, 1000, 1000)
        command("zpool", "sync", pool)
        return final

    # Partial directories stay private and are reusable after process or power failure.
    partial = inbox / (".transport-" + delivery + ".partial")
    if partial.exists() or partial.is_symlink():
        private_directory(partial)
        require(
            partial.stat().st_dev == workspace.stat().st_dev,
            "Partial delivery is on another filesystem",
        )
        walk_tree(partial)  # Refuse nested mounts before removing an interrupted copy.
        shutil.rmtree(partial)
    partial.mkdir(mode=0o700)
    require(
        walk_tree(source / "files", partial) == manifest["entries"]
        and walk_tree(partial) == manifest["entries"],
        "Inbox file verification failed; transport retained",
    )
    for directory, dirs, files in os.walk(partial, followlinks=False):
        for name in dirs + files:
            os.chown(Path(directory) / name, 1000, 1000, follow_symlinks=False)
    if not receipt.exists():
        write_metadata(receipt, raw)
    sync_directory(receipts)
    command("zpool", "sync", pool)
    partial.rename(final)
    os.chown(final, 1000, 1000)
    sync_directory(inbox)
    command("zpool", "sync", pool)
    return final


def stage(pair, source, discover):
    pool = pair_pool(pair)
    # Inspect all source data and connected identities before replacing any existing delivery.
    entries = walk_tree(source)
    require(bool(entries), "Transport directory is empty; nothing staged")
    size = sum(entry.get("size", 0) for entry in entries)
    require(
        pool not in output("zpool", "list", "-H", "-o", "name").splitlines(),
        "Export the selected pair before staging",
    )
    guid = output("python3", discover, "--pair", pair, "--guid")
    require(
        re.fullmatch(r"[1-9][0-9]*", guid) is not None, "Invalid discovered pool GUID"
    )
    print(
        f"Stage {len(entries)} entries ({size} bytes) on {pool}. Existing transport will be destroyed.",
        flush=True,
    )
    require(input("Continue? [y/N] ").lower() in ("y", "yes"), "Staging cancelled")
    command(
        "zpool",
        "import",
        "-N",
        "-d",
        "/dev/disk/by-partuuid",
        "-o",
        "cachefile=none",
        guid,
    )
    try:
        require(
            output("zpool", "get", "-H", "-o", "value", "guid", pool) == guid,
            "Imported pool identity differs",
        )
        data = validate_pair(pool, pair)
        require(
            prop(data, "mounted") == "no" and prop(data, "keystatus") == "unavailable",
            "Workspace must remain locked and unmounted",
        )
        leaves = mirror_leaves(output("zpool", "status", "-g", pool))
        require(
            output("zpool", "list", "-H", "-o", "health", pool) == "ONLINE"
            and all(row[2:] == ("ONLINE", "0", "0", "0") for row in leaves),
            "Require both mirror members ONLINE without device errors",
        )
        transport = pool + "/transport"
        if transport in datasets(pool):
            validate_transport(pool)
            require(prop(transport, "mounted") == "no", "Transport is already mounted")
            command("zfs", "destroy", transport)
        options = [
            item
            for name, value in POLICY.items()
            for item in ("-o", name + "=" + value)
        ]
        # Bound temporary usage while allowing arbitrary payload sizes and metadata overhead.
        quota = size + max(64 * 1024 * 1024, size // 5)
        command("zfs", "create", "-u", *options, "-o", "quota=" + str(quota), transport)
        with mounted_transport(pool) as directory:
            manifest = package(source, directory, entries)
        command("zpool", "sync", pool)
        print(f"Staged {manifest['delivery']}. Local transport files are unchanged.")
    finally:
        command("zpool", "export", pool)
    print("Pair exported; safe to disconnect.")


def import_transport(pair):
    pool = pair_pool(pair)
    require(
        pool in output("zpool", "list", "-H", "-o", "name").splitlines(),
        "Mount the active workspace with start or workspace-mount first",
    )
    data = validate_pair(pool, pair)
    require(
        prop(pool, "org.level1vault:state") == "active"
        and prop(data, "readonly") == "off",
        "Transport import requires the active writable pair; replicas are refused",
    )
    require(
        prop(data, "mounted") == "yes"
        and prop(data, "mountpoint") == "/mnt/workspace"
        and output("findmnt", "-n", "-o", "SOURCE", "--mountpoint", "/mnt/workspace")
        == data,
        "Mount the selected active workspace at /mnt/workspace first",
    )
    with mounted_transport(pool, readonly=True) as directory:
        final = publish_inbox(directory, Path("/mnt/workspace"), pool)
    # The verified destination is durable before exact, nonrecursive source deletion.
    command("zfs", "destroy", validate_transport(pool))
    command("zpool", "sync", pool)
    print(f"Imported and verified {final}; transport dataset removed.")
