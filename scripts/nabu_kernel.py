"""Select a compiled Nabu kernel's normal/debug slot and pinned boot arguments."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from nabu_paths import PROJECT, kernel_input


def select(input_directory=None, project=PROJECT):
    directory = input_directory or kernel_input()
    profile_bytes = (directory/'sources.json').read_bytes()
    profile = json.loads(profile_bytes)
    if not isinstance(profile, dict):
        raise ValueError('Kernel source profile must be an object')
    release = (directory/'kernelrelease').read_text().strip()
    boot = profile.get('boot', {'mode': 'debug'})
    if not isinstance(boot, dict):
        raise ValueError('Kernel boot selection must be an object')
    mode = boot.get('mode')
    if mode == 'normal':
        if set(boot) != {'mode', 'cmdline'} or not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:-rc[1-9][0-9]*)?-nabu[0-9]+\+?', release):
            raise ValueError('Normal kernel profile/release mismatch')
        pin = boot['cmdline']
        if not isinstance(pin, dict) or set(pin) != {'path', 'sha256'}:
            raise ValueError('Kernel command line requires an exact source pin')
        relative = Path(pin['path'])
        path = (project/relative).resolve(strict=True)
        if relative.is_absolute() or not path.is_relative_to((project/'kernel').resolve()):
            raise ValueError('Kernel command-line pin must be under kernel/')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != pin['sha256']:
            raise ValueError('Pinned kernel command line changed')
        extra = data.decode()
        slot = 'local'
    elif mode == 'debug' and set(boot) == {'mode'}:
        if not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:-rc[1-9][0-9]*)?-nabu-debug[0-9]+\+?', release):
            raise ValueError('Debug kernel profile/release mismatch')
        extra = (project/'kernel/debug.cmdline').read_text()
        slot = 'debug'
    else:
        raise ValueError('Unsupported kernel boot mode')
    if any(arg.startswith('earlycon=efifb') or arg == 'keep_bootcon' for arg in extra.split()):
        raise ValueError('Unsafe EFI early-console argument')
    if any(arg.startswith(('root=', 'rootfstype=', 'logo.')) for arg in extra.split()):
        raise ValueError('Kernel verbosity profile must not override board/root or image preferences')
    if mode == 'normal' and any(arg in extra.split() for arg in ('initcall_debug', 'ignore_loglevel')):
        raise ValueError('Normal kernel profile requests forced verbose diagnostics')
    return {'schema': 1, 'mode': mode, 'slot': slot, 'release': release,
            'package': 'linux-nabu-'+slot, 'uki': 'arch-linux-nabu-'+slot+'.efi',
            'extra_cmdline': extra.strip(),
            'source_profile_sha256': hashlib.sha256(profile_bytes).hexdigest()}


def commandline(board, selection):
    return board.rstrip()+'\n'+selection['extra_cmdline']+'\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--field', choices=('mode', 'slot', 'package', 'uki'))
    parser.add_argument('--cmdline-root', type=Path)
    args = parser.parse_args()
    try:
        selection = select()
        if args.field:
            print(selection[args.field])
        elif args.cmdline_root:
            print(commandline(args.cmdline_root.read_text(), selection), end='')
        else:
            print(json.dumps(selection, indent=2))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f'Kernel selection refused: {exc}')
