#!/usr/bin/env python3
"""Read-only structural inspection of an ARM64 Nabu UKI (OS-05)."""
import argparse
from pathlib import Path
import re
import struct


def read_sections(path, root='PARTLABEL=userdata', require_ext4=True, filesystem=None):
    data = Path(path).read_bytes()
    def unpack(fmt, offset):
        try:
            return struct.unpack_from(fmt, data, offset)
        except struct.error as exc:
            raise ValueError('Truncated PE header') from exc
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('Not a DOS/PE executable')
    offset, = unpack('<I', 0x3c)
    if offset > len(data) - 24 or data[offset:offset+4] != b'PE\0\0':
        raise ValueError('Missing PE signature')
    machine, count = unpack('<HH', offset+4)
    optional, = unpack('<H', offset+20)
    if machine != 0xaa64 or count == 0 or count > 96:
        raise ValueError('Not a supported ARM64 PE executable')
    table = offset + 24 + optional
    if table + 40*count > len(data):
        raise ValueError('Truncated PE section table')
    sections = {}
    ranges = []
    for index in range(count):
        entry = table + index*40
        name = data[entry:entry+8].rstrip(b'\0')
        virtual, _, size, start = unpack('<IIII', entry+8)
        if name in sections or start > len(data) or size > len(data)-start:
            raise ValueError('Invalid or duplicate PE section')
        if size:
            if start < table + 40*count or any(start < end and begin < start+size for begin, end in ranges):
                raise ValueError('Overlapping PE section or section table')
            ranges.append((start, start+size))
        if name in (b'.linux', b'.initrd', b'.dtb', b'.cmdline', b'.uname') and virtual > size:
            raise ValueError('Truncated UKI section payload')
        sections[name] = data[start:start+min(virtual, size)]
    required = (b'.linux', b'.initrd', b'.dtb', b'.cmdline', b'.uname')
    for name in required:
        if not sections.get(name):
            raise ValueError(f'Missing or empty {name.decode()} section')
    release = sections[b'.uname'].rstrip(b'\0\n').decode('ascii')
    if not re.fullmatch(r'[A-Za-z0-9._+-]+', release):
        raise ValueError('Invalid kernel release')
    cmdline = sections[b'.cmdline'].rstrip(b'\0').decode('ascii').split()
    if any('\0' in argument for argument in cmdline):
        raise ValueError('Embedded NUL in kernel command line')
    if [argument for argument in cmdline if argument.startswith('root=')] != ['root='+root]:
        raise ValueError('Unexpected or conflicting root target')
    filesystems = [argument for argument in cmdline if argument.startswith('rootfstype=')]
    expected_fs = filesystem or 'ext4'
    if expected_fs not in ('ext4', 'btrfs'):
        raise ValueError('Unsupported root filesystem')
    if (filesystems or require_ext4 or filesystem) and filesystems != ['rootfstype='+expected_fs]:
        raise ValueError('Unexpected or conflicting root filesystem type')
    rootflags = [argument for argument in cmdline if argument.startswith('rootflags=')]
    if expected_fs == 'btrfs' and rootflags != ['rootflags=subvol=@']:
        raise ValueError('Unexpected or conflicting Btrfs root subvolume')
    if 'keep_bootcon' in cmdline or any(argument.startswith('earlycon=efifb') for argument in cmdline):
        raise ValueError('Known unsafe EFI early-console argument')
    return sections


def inspect(path, filesystem='ext4'):
    return read_sections(path, filesystem=filesystem)[b'.uname'].rstrip(b'\0\n').decode('ascii')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('uki', type=Path, nargs='+')
    parser.add_argument('--filesystem', choices=('ext4','btrfs'), default='ext4')
    args = parser.parse_args()
    for path in args.uki:
        print(f'{path.name}: ARM64 UKI, kernel {inspect(path, args.filesystem)}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, UnicodeError) as exc:
        raise SystemExit(f'UKI inspection failed: {exc}')
