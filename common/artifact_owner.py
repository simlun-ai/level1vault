#!/usr/bin/env python3
"""Publish explicit host metadata paths with the invoking sudo user's ownership."""

import os
import stat
import sys
from pathlib import Path


def open_metadata(path):
    parts = path.absolute().parts
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
            )
            os.close(parent)
            parent = child
        return os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
    finally:
        os.close(parent)


def publish(*paths):
    # Change only explicit regular files/directories; never follow symlink ancestors.
    if os.geteuid() != 0 or "SUDO_UID" not in os.environ:
        return
    uid, gid = int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"])
    for path in map(Path, paths):
        try:
            fd = open_metadata(path)
        except FileNotFoundError:
            continue
        try:
            info = os.fstat(fd)
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise ValueError(f"Refusing multiply linked metadata: {path}")
            if info.st_uid == 0 and (
                stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
            ):
                os.fchown(fd, uid, gid)
        finally:
            os.close(fd)


if __name__ == "__main__":
    publish(*sys.argv[1:])
