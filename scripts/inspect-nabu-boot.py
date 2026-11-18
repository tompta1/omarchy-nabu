#!/usr/bin/env python3
"""Inspect the reviewed GRUB entry forms and their UKIs/modules, offline (OS-05)."""
import argparse
import importlib.util
from pathlib import Path
import re
import uuid
from nabu_grub import kernel_entries

spec = importlib.util.spec_from_file_location('nabu_uki', Path(__file__).with_name('inspect-nabu-uki.py'))
uki = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uki)
ENTRIES = kernel_entries()


def module_paths(text):
    """Validate depmod's relative paths, shared by mounted and image readers."""
    lines = text.splitlines()
    if not lines:
        raise ValueError('Module dependency metadata is empty')
    paths = set()
    for line in lines:
        if line.count(':') != 1:
            raise ValueError('Malformed module dependency entry')
        target, dependencies = line.split(':', 1)
        if not target or any(ch.isspace() for ch in target):
            raise ValueError('Malformed module path')
        paths.add(target)
        paths.update(dependencies.split())
    for name in sorted(paths):
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or not name.endswith(('.ko', '.ko.xz', '.ko.zst', '.ko.gz')):
            raise ValueError('Unsafe module dependency path')
    return sorted(paths)


def check_modules(modules):
    """Check every path in depmod metadata stays within and exists in its tree."""
    base = modules.resolve(strict=True)
    if not (modules / 'kernel').is_dir():
        raise ValueError('Kernel module directory is missing')
    metadata = modules / 'modules.dep'
    if not metadata.is_file():
        raise ValueError('Module dependency metadata is missing')
    paths = module_paths(metadata.read_text())
    for name in paths:
        path = Path(name)
        try:
            resolved = (modules / path).resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError(f'Missing module file: {name}') from exc
        if not resolved.is_relative_to(base) or not resolved.is_file():
            raise ValueError(f'Invalid module file: {name}')
    return len(paths)


def parse_menu(text, boot_uuid, primary='debug'):
    expected = uuid.UUID(boot_uuid)
    identities = []
    entries = kernel_entries(primary)
    for label, filename in entries.items():
        pattern = (r"(?m)^menuentry '" + re.escape(label) + r"' \{\n"
                   r"  search --no-floppy --fs-uuid --set=root ([0-9a-fA-F-]+)\n"
                   r"  chainloader /EFI/arch/" + re.escape(filename) + r"\n\}")
        matches = re.findall(pattern, text)
        if len(matches) != 1 or text.count("menuentry '" + label + "'") != 1:
            raise ValueError(f'Unexpected GRUB entry: {label}')
        identities.append(uuid.UUID(matches[0]))
    if any(identity != expected for identity in identities):
        raise ValueError('GRUB filesystem UUID differs from supplied boot filesystem')
    return entries


def inspect(boot, root, boot_uuid, primary='debug'):
    entries = parse_menu((boot / 'grub2/grub.cfg').read_text(), boot_uuid, primary)
    results = []
    for label, filename in entries.items():
        release = uki.inspect(boot / 'EFI/arch' / filename)
        modules = root / 'usr/lib/modules' / release
        count = check_modules(modules)
        results.append(f'{label}: {filename}; {count} module files for {release}')
    for result in results:
        print(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boot', type=Path, required=True, help='Directory containing grub2 and EFI')
    parser.add_argument('--root', type=Path, required=True, help='Directory containing usr/lib/modules')
    parser.add_argument('--boot-uuid', required=True, help='Independently measured boot filesystem UUID')
    parser.add_argument('--primary', choices=('debug', 'local'), default='debug')
    args = parser.parse_args()
    inspect(args.boot, args.root, args.boot_uuid, args.primary)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, UnicodeError) as exc:
        raise SystemExit(f'Boot inspection failed: {exc}')
