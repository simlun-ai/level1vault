"""Stream USB image writes and verify the written extent by readback."""

import hashlib
import os
import subprocess
import time
from pathlib import Path

from usb_devices import require


def readback(disk: Path, size: int, expected: str) -> None:
    # Read exactly the written extent after flushing the device cache.
    subprocess.run(["blockdev", "--flushbufs", str(disk)], check=True)
    digest = hashlib.sha256()
    remaining = size
    with disk.open("rb", buffering=0) as stream:
        while remaining:
            block = stream.read(min(4 * 1024 * 1024, remaining))
            require(bool(block), f"Short USB readback: {disk}")
            digest.update(block)
            remaining -= len(block)
    require(digest.hexdigest() == expected, f"USB readback checksum mismatch: {disk}")


def write_image(image: Path, disk: Path) -> None:
    # Hash the bytes being written without a separate source verification pass.
    size = image.stat().st_size
    digest = hashlib.sha256()
    written = 0
    progress = time.monotonic()
    with (
        image.open("rb", buffering=0) as source,
        disk.open("r+b", buffering=0) as target,
    ):
        while written < size:
            block = source.read(min(4 * 1024 * 1024, size - written))
            require(bool(block), f"Source image became shorter: {image}")
            view = memoryview(block)
            while view:
                count = target.write(view)
                require(count is not None and count > 0, f"Short USB write: {disk}")
                view = view[count:]
            digest.update(block)
            written += len(block)
            if time.monotonic() - progress >= 1:
                print(f"Writing {disk}: {written}/{size} bytes", flush=True)
                progress = time.monotonic()
        require(not source.read(1), f"Source image grew during writing: {image}")
        os.fsync(target.fileno())
    print(f"Reading back {disk}: {size} bytes", flush=True)
    readback(disk, size, digest.hexdigest())
