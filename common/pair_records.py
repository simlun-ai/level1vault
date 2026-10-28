"""Keep small recovery identities independently of disposable image files."""

import fcntl
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from artifact_owner import publish
from member_recovery import partition_table, require

ROOT = Path(__file__).resolve().parents[1]


def records_root():
    return Path(os.environ.get("RECORDS_DIR", ROOT / "records")).resolve()


def validate_membership(record, pair):
    require(re.fullmatch(r"[1-9][0-9]*", pair) is not None, "Invalid pair ID")
    require(
        record["format"] == 1 and record["pair"] == pair,
        "Recovery record belongs to another pair",
    )
    require(
        re.fullmatch(r"[1-9][0-9]*", record["pool_guid"]) is not None,
        "Invalid pool GUID",
    )
    for member in ("a", "b"):
        row = record["members"][member]
        require(
            str(uuid.UUID(row["zfs_partuuid"])) == row["zfs_partuuid"],
            "Invalid member PARTUUID",
        )
        require(
            re.fullmatch(r"[0-9A-F]{4}-[0-9A-F]{4}", row["esp_uuid"]) is not None,
            "Invalid FAT UUID",
        )
        require(
            type(row["minimum_bytes"]) is int and row["minimum_bytes"] > 0,
            "Invalid partition size",
        )
    require(
        record["members"]["a"]["zfs_partuuid"]
        != record["members"]["b"]["zfs_partuuid"],
        "Duplicate member",
    )
    require(
        record["members"]["a"]["esp_uuid"] != record["members"]["b"]["esp_uuid"],
        "Duplicate FAT UUID",
    )
    return record


def write_json(path, value):
    # Publish complete JSON atomically, including when called under sudo.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, prefix="." + path.name, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(value, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        publish(temporary)
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def locked_pair(root, pair):
    require(re.fullmatch(r"[1-9][0-9]*", pair) is not None, "Invalid pair ID")
    directory = root / "pairs" / pair
    directory.mkdir(parents=True, exist_ok=True)
    publish(root, root / "pairs", directory)
    with (directory / ".lock").open("a") as lock:
        publish(directory / ".lock")
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield directory


def require_separate(root, staging):
    root, staging = root.resolve(), staging.resolve()
    require(
        not root.is_relative_to(staging) and not staging.is_relative_to(root),
        "Keep RECORDS_DIR and build/recovery output in separate directories",
    )


def save_pair(out, pair, root) -> dict[str, Any]:
    # Export only initialized identities; images are read once to record real bounds.
    staging = out.parent.parent if out.parent.name == "pairs" else out
    require_separate(root, staging)
    require(
        (out / "READY").is_file() and not (out / "INITIALIZING").exists(),
        "Expected initialized pair with READY",
    )
    info = dict(
        line.split("=", 1)
        for line in (out / "WORKSPACE-INFO.txt").read_text().splitlines()
        if "=" in line
    )
    require(
        info["pair"] == pair and info["pool"] == "level1-workspace-" + pair,
        "Pair metadata mismatch",
    )
    images = json.loads((out / "images.json").read_text())
    require(images["format"] == 1, "Unsupported image manifest")
    members = {}
    for member in ("a", "b"):
        part = partition_table(out / f"usb-{member}.img")["partitions"][1]
        row = images["images"][member]
        require(
            part["uuid"].lower() == row["zfs_partuuid"], "Image and manifest disagree"
        )
        members[member] = dict(
            zfs_partuuid=row["zfs_partuuid"],
            esp_uuid=row["esp_uuid"],
            minimum_bytes=part["size"] * 512,
        )
    record = validate_membership(
        dict(format=1, pair=pair, pool_guid=info["pool_guid"], members=members), pair
    )
    with locked_pair(root, pair) as directory:
        current = directory / "current.json"
        if current.exists():
            require(
                json.loads(current.read_text()) == record,
                "Recorded membership differs; never overwrite it with old images",
            )
        else:
            initial = directory / "initial.json"
            if initial.exists():
                require(
                    json.loads(initial.read_text()) == record, "Initial record differs"
                )
            else:
                write_json(initial, record)
            write_json(current, record)
    print(
        f"Keep and back up {directory}; build images are not needed for member recovery."
    )
    return record


def accept_replacement(root, pair, replacement, confirmed):
    # Advancing membership is an explicit operator acknowledgement of live recovery.
    require(
        confirmed,
        "Set CONFIRMED=yes only after workspace-replace succeeds and files are read-checked",
    )
    with locked_pair(root, pair) as directory:
        pending = directory / "replacements" / (str(uuid.UUID(replacement)) + ".json")
        change = json.loads(pending.read_text())
        before = validate_membership(change["before"], pair)
        after = validate_membership(change["after"], pair)
        current = directory / "current.json"
        actual = json.loads(current.read_text())
        require(
            actual in (before, after),
            "Current membership changed; inspect recovery history",
        )
        write_json(current, after)
    print(
        f"Current membership saved: {current}. Back up the complete records directory."
    )
