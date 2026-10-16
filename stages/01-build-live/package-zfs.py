#!/usr/bin/env python3
"""Package target-kernel ZFS modules independently of DKMS ownership."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def build_package(kernel, version, architecture, modules, copyright_file, output):
    # Validate package metadata and require actual compiled ZFS module files.
    for value in (kernel, version, architecture):
        if not re.fullmatch(r'[A-Za-z0-9.+:~_-]+', value):
            raise ValueError('Invalid package metadata')
    files = sorted(path for path in modules.iterdir()
                   if re.fullmatch(r'.+\.ko(?:\.(?:xz|zst|gz))?', path.name))
    if not files or not any(re.fullmatch(r'zfs\.ko(?:\.(?:xz|zst|gz))?', path.name) for path in files):
        raise ValueError('Compiled ZFS modules are missing')
    if any(not path.is_file() or path.is_symlink() for path in files):
        raise ValueError('Expected regular module files')

    # Give the standalone package its own canonical module directory.
    with tempfile.TemporaryDirectory(prefix='level1vault-module-package-') as temporary:
        root = Path(temporary)
        target = root / 'usr/lib/modules' / kernel / 'extra/level1vault'
        target.mkdir(parents=True)
        for path in files:
            shutil.copyfile(path, target / path.name)
            (target / path.name).chmod(0o644)
        docs = root / 'usr/share/doc/level1vault-zfs-modules'
        docs.mkdir(parents=True)
        shutil.copyfile(copyright_file, docs / 'copyright')

        # Declare the exact kernel and userland required by the compiled modules.
        control = root / 'DEBIAN'
        control.mkdir()
        (control / 'control').write_text(
            'Package: level1vault-zfs-modules\n'
            f'Version: {version}+level1vault.1\n'
            f'Architecture: {architecture}\n'
            'Maintainer: level1vault builders <ai@simlun.se>\n'
            f'Depends: linux-image-{kernel}, zfsutils-linux (= {version}), kmod\n'
            f'Provides: zfs-modules (= {version})\n'
            'Section: contrib/kernel\nPriority: optional\n'
            'Description: Prebuilt ZFS modules for the level1vault live kernel\n'
            ' Built from Debian ZFS sources; no runtime DKMS toolchain is required.\n')
        for name in ('postinst', 'postrm'):
            path = control / name
            path.write_text('#!/bin/sh\nset -eu\n'
                            f'if [ -d /lib/modules/{kernel} ]; then depmod -a {kernel}; fi\n')
            path.chmod(0o755)
        # Avoid inherited setgid bits or restrictive build umasks in the package.
        root.chmod(0o755)
        for directory in root.rglob('*'):
            if directory.is_dir():
                directory.chmod(0o755)
        subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(root), str(output)], check=True)


def main():
    # Read the paths supplied by the live-build hook.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel')
    parser.add_argument('version')
    parser.add_argument('architecture')
    parser.add_argument('modules', type=Path)
    parser.add_argument('copyright', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    build_package(args.kernel, args.version, args.architecture, args.modules, args.copyright, args.output)


if __name__ == '__main__':
    main()
