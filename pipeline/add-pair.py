#!/usr/bin/env python3
"""Assemble and initialize one additional empty replica using an existing ISO."""
import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def prepare(build, out, pair):
    # Reject invalid or previously used destinations before doing any work.
    if not re.fullmatch(r'[1-9][0-9]*', pair):
        raise ValueError('PAIR must be a positive integer without leading zeros')
    if not (build / 'live/live-image-amd64.hybrid.iso').is_file():
        raise ValueError('BUILD_DIR must contain the existing live ISO')
    if out.exists() or out.is_symlink() or not out.resolve().is_relative_to(build.resolve()) or ',' in str(out.resolve()):
        raise ValueError('OUT must be a new directory beneath BUILD_DIR without commas')

    # Authenticate before assembly, then retain the original stage safety checks.
    sudo = shlex.split(os.environ.get('SUDO', 'sudo'))
    if sudo:
        subprocess.run([*sudo, '-v'], check=True)
    subprocess.run(['bash', str(ROOT / 'pipeline/buildkit.sh'), 'assemble', str(build), str(out)], check=True)
    subprocess.run([*sudo, str(ROOT / 'stages/03-initialize-pair/initialize.sh'), str(out), pair], check=True)
    subprocess.run([sys.executable, str(ROOT / 'pipeline/records.py'), 'save', '--out', str(out), '--pair', pair], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--pair', required=True)
    args = parser.parse_args()
    try:
        prepare(args.build_dir, args.out, args.pair)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'Additional pair preparation stopped: {error}. Preserve partial outputs and inspect pools/loops before retrying.')
