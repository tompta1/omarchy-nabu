#!/usr/bin/env python3
"""Suppress automatic boot actions for an isolated, explicitly assembled image."""
import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess


def boot_hook(name):
    return name.endswith('.hook') and any(part in name for part in
                                         ('limine', 'mkinitcpio', 'linux-nabu', 'snapper'))


def prepare(root, output, packages):
    root, output = Path(root), Path(output)
    if not (root/'.omarchy-nabu-build-root').is_file():
        raise ValueError('Use a marked isolated build root')
    if output.is_symlink() or not output.is_dir() or any(output.iterdir()):
        raise ValueError('Use a new empty private hook directory')
    names = set()
    for directory in ('usr/share/libalpm/hooks', 'etc/pacman.d/hooks'):
        names.update(p.name for p in (root/directory).glob('*.hook') if boot_hook(p.name))
    for package in packages:
        listing = subprocess.check_output(['bsdtar', '-tf', str(package)], text=True)
        for entry in listing.splitlines():
            entry = entry.removeprefix('./')
            path = PurePosixPath(entry)
            if str(path.parent) in ('usr/share/libalpm/hooks', 'etc/pacman.d/hooks') and boot_hook(path.name):
                if not re.fullmatch(r'[A-Za-z0-9_.+-]+', path.name):
                    raise ValueError('Unsafe package hook filename')
                names.add(path.name)
    for name in sorted(names):
        (output/name).symlink_to('/dev/null')
    return sorted(names)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('packages', type=Path, nargs='+')
    args = parser.parse_args()
    try:
        names = prepare(args.root, args.output, args.packages)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit('Build hook masking refused: '+str(error))
    print('Deferred boot hooks until explicit image assembly: '+', '.join(names))
