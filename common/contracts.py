"""Wire data shapes used by the shared helpers; runtime validators remain authoritative."""

from typing import Literal, Protocol, TypedDict


class Query(Protocol):
    def __call__(self, *args: str) -> str: ...


class DiskIdentity(TypedDict):
    name: str
    type: str
    size: int
    device_id: int


class Disk(DiskIdentity, total=False):
    children: list["Disk"]
    mountpoints: list[str | None]
    ro: bool | int
    tran: str | None
    model: str | None
    serial: str | None
    partuuid: str
    uuid: str


class Partition(TypedDict):
    start: int
    node: str
    type: str
    uuid: str
    size: int


class TableOptions(TypedDict, total=False):
    sectorsize: int


class PartitionTable(TableOptions):
    label: str
    partitions: list[Partition]


class Member(TypedDict):
    zfs_partuuid: str
    esp_uuid: str
    minimum_bytes: int


class Membership(TypedDict):
    format: int
    pair: str
    pool_guid: str
    members: dict[str, Member]


class RecoveryRecord(TypedDict):
    format: int
    pair: str
    member: str
    pool_guid: str
    minimum_bytes: int
    failed_partuuid: str
    survivor_partuuid: str
    new_partuuid: str
    esp_uuid: str


class ReleasePayload(TypedDict):
    format: int
    kernel: str
    zfs_package: str
    source_commit: str
    files: dict[str, str]


class Release(ReleasePayload):
    release_id: str


class DirectoryEntry(TypedDict):
    path: str
    type: Literal["directory"]


class SymlinkEntry(TypedDict):
    path: str
    type: Literal["symlink"]
    target: str


class FileEntry(TypedDict):
    path: str
    type: Literal["file"]
    size: int
    sha256: str


Entry = DirectoryEntry | SymlinkEntry | FileEntry


class Manifest(TypedDict):
    format: int
    delivery: str
    entries: list[Entry]
