#!/usr/bin/env python3
"""Identify installed build tools and development headers for removal."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def build_package(name: str):
    # Retain the GCC version metadata needed by runtime libraries.
    name = name.split(":", 1)[0]
    if re.fullmatch(r"gcc-\d+-base", name):
        return False

    # Reject toolchains regardless of when or why live-build installed them.
    return (
        name in {"build-essential", "dkms", "zfs-dkms", "dpkg-dev", "make"}
        or re.fullmatch(r"(?:gcc|g\+\+|cpp|clang)(?:-.*)?", name) is not None
        or name.startswith(("linux-headers-", "linux-kbuild-", "binutils"))
        or name.endswith("-dev")
    )


def removal_plan(inventory: str):
    # Select installed packages only, preserving exact multiarch package names.
    return sorted(
        name
        for name, status in (row.split("\t") for row in inventory.splitlines())
        if status == "installed" and build_package(name)
    )


if __name__ == "__main__":
    for package in removal_plan(Path(sys.argv[1]).read_text()):
        print(package)
