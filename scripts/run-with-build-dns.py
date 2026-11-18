#!/usr/bin/env python3
"""Run a build command with temporary chroot DNS, restoring runtime configuration."""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import tempfile


@contextmanager
def temporary_resolver(root, resolver):
    root=Path(root).resolve()
    resolver=Path(resolver)
    if root==Path('/') or not (root/'.omarchy-nabu-build-root').is_file():
        raise ValueError('Use an explicitly marked isolated build root')
    target=root/'etc/resolv.conf'
    if target.exists() and not target.is_file():
        raise ValueError('Build resolver is not a regular file or file symlink')
    data=resolver.read_bytes()
    with tempfile.TemporaryDirectory(prefix='.nabu-build-dns-',dir=target.parent) as folder:
        backup=Path(folder)/'original'
        had_original=target.is_symlink() or target.exists()
        if had_original: os.replace(target,backup)
        try:
            target.write_bytes(data)
            target.chmod(0o644)
            yield
        finally:
            target.unlink(missing_ok=True)
            if had_original: os.replace(backup,target)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--resolver',type=Path,default=Path('/etc/resolv.conf'))
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    if not command: parser.error('A build command is required')
    try:
        with temporary_resolver(args.root,args.resolver):
            result=subprocess.run(command)
        raise SystemExit(result.returncode)
    except (OSError,ValueError) as error:
        raise SystemExit('Temporary build DNS refused: '+str(error))
