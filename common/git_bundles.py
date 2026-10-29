"""Create self-contained Git deliveries and update managed offline working checkouts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlsplit

NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
OID = r"[0-9a-f]{40}|[0-9a-f]{64}"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def source_identity(url: str) -> tuple[str, str]:
    require(bool(url) and not url.startswith("-"), "REPO must be a repository URL")
    scp = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):(.+)", url) if "://" not in url else None
    if scp:
        host, path = scp.groups()
        identity = host.lower() + "/" + path.lstrip("/")
    else:
        parsed = urlsplit(url)
        require(
            parsed.scheme in ("https", "http", "ssh", "file"),
            "Use an HTTPS, SSH or file repository URL",
        )
        require(
            not parsed.query and not parsed.fragment and not parsed.password,
            "Use Git authentication helpers instead of credentials or tokens in REPO",
        )
        require(
            parsed.scheme == "ssh" or parsed.username is None,
            "Do not embed credentials in REPO",
        )
        path = unquote(parsed.path).rstrip("/")
        if parsed.scheme == "file":
            require(
                parsed.netloc in ("", "localhost") and path.startswith("/"),
                "Invalid file URL",
            )
            identity = Path(path).resolve().as_uri()
        else:
            require(bool(parsed.hostname), "Missing repository hostname")
            port = parsed.port
            default = {"ssh": 22, "http": 80, "https": 443}[parsed.scheme]
            identity = (
                str(parsed.hostname).lower()
                + (f":{port}" if port and port != default else "")
                + "/"
                + path.lstrip("/")
            )
    identity = identity.rstrip("/").removesuffix(".git")
    name = path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    require(
        re.fullmatch(NAME, name) is not None,
        "Repository basename is not a safe directory name",
    )
    return name, identity


def git(
    repo: Path, *args: str, offline: bool = True, check: bool = True
) -> subprocess.CompletedProcess[str]:
    # Ignore caller Git overrides and disable automatic code execution and secondary fetches.
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    if not offline:
        for key in ("GIT_SSH_COMMAND", "GIT_ASKPASS", "GIT_SSH", "GIT_TERMINAL_PROMPT"):
            if key in os.environ:
                env[key] = os.environ[key]
    options = [
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "fetch.recurseSubmodules=false",
        "-c",
        "submodule.recurse=false",
        "-c",
        "maintenance.auto=false",
        "-c",
        "gc.auto=0",
    ]
    if offline:
        env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_NO_LAZY_FETCH="1",
            GIT_TERMINAL_PROMPT="0",
        )
        options += ["-c", "protocol.allow=never", "-c", "protocol.file.allow=always"]
    result = subprocess.run(
        ["git", *options, "-C", str(repo), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode:
        raise ValueError(f"Git {args[0]} failed: {result.stderr.strip()}")
    return result


def text(repo: Path, *args: str, offline: bool = True) -> str:
    return git(repo, *args, offline=offline).stdout.strip()


def refs(repo: Path, prefix: str = "refs/heads", tags: bool = True) -> dict[str, str]:
    roots = [prefix, "refs/tags"] if tags else [prefix]
    rows = text(repo, "for-each-ref", "--format=%(objectname) %(refname)", *roots)
    return {ref: oid for oid, ref in (row.split(" ", 1) for row in rows.splitlines())}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


@dataclass(frozen=True)
class Bundle:
    format: int
    name: str
    source: str
    default_branch: str
    refs: dict[str, str]
    sha256: str


def read_metadata(path: Path) -> Bundle:
    require(path.is_file() and not path.is_symlink(), f"Expected metadata file: {path}")
    raw: object = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("Unsupported Git bundle metadata")
    obj = cast("dict[str, object]", raw)
    require(obj.get("format") == 1, "Unsupported Git bundle metadata")
    name, source, branch, checksum = (
        obj.get(key) for key in ("name", "source", "default_branch", "sha256")
    )
    require(
        isinstance(name, str) and re.fullmatch(NAME, name) is not None,
        "Invalid repository name",
    )
    require(isinstance(source, str) and bool(source), "Missing source identity")
    require(
        isinstance(branch, str) and branch.startswith("refs/heads/"),
        "Invalid default branch",
    )
    require(
        isinstance(checksum, str)
        and re.fullmatch(r"[0-9a-f]{64}", checksum) is not None,
        "Invalid bundle checksum",
    )
    raw_refs = obj.get("refs")
    require(isinstance(raw_refs, dict), "Missing bundled refs")
    typed_refs = cast("dict[str, object]", raw_refs)
    checked: dict[str, str] = {}
    for key, value in typed_refs.items():
        require(
            key.startswith(("refs/heads/", "refs/tags/"))
            and isinstance(value, str)
            and re.fullmatch(OID, value) is not None,
            "Invalid bundled ref",
        )
        checked[key] = cast("str", value)
    require(branch in checked, "Default branch missing from bundle metadata")
    return Bundle(
        1,
        cast("str", name),
        cast("str", source),
        cast("str", branch),
        checked,
        cast("str", checksum),
    )


def add_bundle(url: str, outbox: Path) -> Path:
    name, identity = source_identity(url)
    outbox.mkdir(parents=True, exist_ok=True)
    require(not outbox.is_symlink(), "Outbox must be a real directory")
    bundle, metadata = outbox / (name + ".bundle"), outbox / (name + ".json")
    if (
        bundle.exists()
        or metadata.exists()
        or bundle.is_symlink()
        or metadata.is_symlink()
    ):
        previous = read_metadata(metadata)
        require(
            bundle.is_file()
            and not bundle.is_symlink()
            and previous.name == name
            and previous.source == identity,
            f"Repository name collision or incomplete existing package: {name}",
        )

    # Finish cloning and verification before replacing any previous completed delivery.
    with tempfile.TemporaryDirectory(prefix="level1vault-git-") as directory:
        temporary = Path(directory)
        bare = temporary / "source.git"
        git(
            temporary,
            "clone",
            "--bare",
            "--no-local",
            "--template=",
            "--",
            url,
            str(bare),
            offline=False,
        )
        require(
            text(bare, "rev-parse", "--is-shallow-repository") == "false",
            "Shallow sources are unsupported",
        )
        branch = text(bare, "symbolic-ref", "HEAD")
        advertised = refs(bare)
        require(branch in advertised, "Source has no populated default branch")
        candidate = temporary / (name + ".bundle")
        git(bare, "bundle", "create", str(candidate), "--branches", "--tags", "HEAD")
        empty = temporary / "empty.git"
        git(temporary, "init", "--bare", str(empty))
        git(empty, "bundle", "verify", str(candidate))
        manifest = Bundle(1, name, identity, branch, advertised, digest(candidate))
        # A hash-bound pair detects interrupted publication; ordinary preparation failures preserve old files.
        with tempfile.TemporaryDirectory(prefix=".prepare-", dir=outbox) as pending:
            prepared = Path(pending)
            shutil.copyfile(candidate, prepared / bundle.name)
            (prepared / metadata.name).write_text(
                json.dumps(asdict(manifest), sort_keys=True) + "\n"
            )
            os.replace(prepared / bundle.name, bundle)
            os.replace(prepared / metadata.name, metadata)
    return bundle


@dataclass(frozen=True)
class Prepared:
    bundle: Path
    metadata: Bundle
    verified: Path
    destination: Path


def inspect_bundle(path: Path, scratch: Path, destination: Path) -> Prepared:
    require(
        path.is_file() and not path.is_symlink(), f"Expected regular bundle: {path}"
    )
    metadata = read_metadata(path.with_suffix(".json"))
    require(
        path.stem == metadata.name and digest(path) == metadata.sha256,
        f"Bundle identity/checksum mismatch: {path.name}",
    )
    verified = scratch / (metadata.name + ".git")
    git(scratch, "init", "--bare", str(verified))
    git(verified, "bundle", "verify", str(path))
    # Fetch into an empty repository proves self-containment and checks object connectivity.
    git(
        verified,
        "fetch",
        "--no-tags",
        str(path),
        "refs/heads/*:refs/heads/*",
        "refs/tags/*:refs/tags/*",
    )
    git(verified, "fsck", "--full", "--no-reflogs")
    require(
        refs(verified) == metadata.refs,
        f"Bundle refs differ from metadata: {path.name}",
    )
    git(verified, "symbolic-ref", "HEAD", metadata.default_branch)
    return Prepared(path, metadata, verified, destination / metadata.name)


def inspect_destination(item: Prepared) -> None:
    repo, meta = item.destination, item.metadata
    if not repo.exists() and not repo.is_symlink():
        return
    require(
        repo.is_dir()
        and not repo.is_symlink()
        and (repo / ".git").is_dir()
        and not (repo / ".git").is_symlink(),
        f"Destination is not a standalone checkout: {repo}",
    )
    require(
        text(repo, "rev-parse", "--show-toplevel") == str(repo),
        "Unexpected repository root",
    )
    require(
        text(repo, "config", "--local", "--get", "level1vault.transport-source")
        == meta.source,
        f"Repository source identity differs: {meta.name}",
    )
    require(
        text(repo, "config", "--local", "--get", "level1vault.transport-default-branch")
        == meta.default_branch,
        f"Default branch changed: {meta.name}",
    )
    require(
        text(repo, "symbolic-ref", "HEAD") == meta.default_branch,
        f"Checkout is on another branch: {meta.name}",
    )
    require(
        text(repo, "rev-parse", "--is-shallow-repository") == "false",
        "Shallow destinations are unsupported",
    )
    unsafe = git(
        repo,
        "config",
        "--local",
        "--get-regexp",
        r"^(filter\.|extensions\.partialclone|remote\..*\.promisor)",
        check=False,
    )
    require(
        unsafe.returncode == 1, "Configured filters or partial clones are unsupported"
    )
    require(
        not text(repo, "status", "--porcelain", "--untracked-files=all", "--ignored"),
        f"Local changes or untracked files: {meta.name}",
    )
    # Compare history in the disposable verification repository before changing destination refs.
    git(
        item.verified,
        "fetch",
        "--no-tags",
        str(repo),
        meta.default_branch + ":refs/level1vault/check",
    )
    old = text(repo, "rev-parse", "HEAD")
    result = git(
        item.verified,
        "merge-base",
        "--is-ancestor",
        old,
        meta.refs[meta.default_branch],
        check=False,
    )
    require(
        result.returncode == 0,
        f"Local commits, divergent history or older bundle: {meta.name}",
    )


def apply_bundle(item: Prepared) -> list[str]:
    repo, meta = item.destination, item.metadata
    if not repo.exists():
        # Publish a fully checked-out repository only after clone and configuration succeed.
        with tempfile.TemporaryDirectory(
            prefix=".git-import-", dir=repo.parent
        ) as pending:
            candidate = Path(pending) / "checkout"
            git(
                repo.parent,
                "clone",
                "--no-local",
                "--no-recurse-submodules",
                "--template=",
                "--branch",
                meta.default_branch.removeprefix("refs/heads/"),
                "--",
                str(item.verified),
                str(candidate),
            )
            git(candidate, "config", "remote.origin.url", str(item.bundle))
            git(candidate, "config", "level1vault.transport-source", meta.source)
            git(
                candidate,
                "config",
                "level1vault.transport-default-branch",
                meta.default_branch,
            )
            candidate.rename(repo)
        return []

    existing_tags = refs(repo, "refs/tags", tags=False)
    incoming_tags = {
        ref: oid for ref, oid in meta.refs.items() if ref.startswith("refs/tags/")
    }
    warnings = [
        f"{meta.name}: preserved changed/deleted tag {ref}"
        for ref, oid in existing_tags.items()
        if incoming_tags.get(ref) != oid
    ]
    tags = [ref + ":" + ref for ref in incoming_tags if ref not in existing_tags]
    git(
        repo,
        "fetch",
        "--atomic",
        "--no-tags",
        "--prune",
        str(item.verified),
        "+refs/heads/*:refs/remotes/origin/*",
        *tags,
    )
    git(repo, "config", "remote.origin.url", str(item.bundle))
    git(
        repo,
        "merge",
        "--ff-only",
        "--no-edit",
        "refs/remotes/origin/" + meta.default_branch.removeprefix("refs/heads/"),
    )
    return warnings


def import_bundles(source: Path, destination: Path) -> bool:
    require(
        source.is_dir() and not source.is_symlink(), "Expected a git-bundles directory"
    )
    paths = sorted(source.glob("*.bundle"))
    require(bool(paths), "No .bundle files found")
    require(
        {p.stem for p in source.glob("*.json")} == {p.stem for p in paths},
        "Bundle metadata pairs are incomplete",
    )
    require(not destination.is_symlink(), "Destination must be a real directory")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".git-verify-", dir=destination
    ) as directory:
        # Preflight the entire batch before any managed repository is updated.
        prepared = [
            inspect_bundle(path, Path(directory), destination) for path in paths
        ]
        for item in prepared:
            inspect_destination(item)
        success = True
        for item in prepared:
            try:
                # Recheck local work immediately before applying this repository.
                inspect_destination(item)
                for warning in apply_bundle(item):
                    print(warning)
                print(f"Updated {item.destination}")
            except (ValueError, OSError) as error:
                print(f"FAILED {item.metadata.name}: {error}")
                success = False
        return success
