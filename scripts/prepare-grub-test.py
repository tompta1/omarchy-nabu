#!/usr/bin/env python3
"""Prepare a one-shot GRUB test bundle without writing to the tablet."""
import argparse
from pathlib import Path
from nabu_layout import load_layout
from nabu_paths import PROJECT, project_relative

ENTRY = 'nabu-grub216-test'
TESTS = {
    'grub216': (ENTRY,'Omarchy Nabu: GRUB 2.16 one-shot test','/EFI/omarchy-nabu/grub-2.16-candidate.efi'),
    'stock': ('nabu-stock-test','Omarchy Nabu: stock kernel one-shot test','/EFI/arch/arch-linux-nabu-stock.efi'),
    'kernel-normal': ('nabu-normal-test','Omarchy Nabu: normal kernel one-shot test','/EFI/arch/arch-linux-nabu-local.efi'),
    'iris-debug3': ('nabu-iris-debug3','Omarchy Nabu: Iris diagnostic one-shot test','/EFI/arch/arch-linux-nabu-debug-next.efi'),
    'iris-debug4': ('nabu-iris-debug4','Omarchy Nabu: Iris PM trace one-shot test','/EFI/arch/arch-linux-nabu-debug-pm.efi'),
    'iris-debug5': ('nabu-iris-debug5','Omarchy Nabu: Iris retained-context one-shot test','/EFI/arch/arch-linux-nabu-debug-next.efi'),
    'iris-debug6': ('nabu-iris-debug6','Omarchy Nabu: Iris balanced PM one-shot test','/EFI/arch/arch-linux-nabu-debug-next.efi'),
}
RETIRED_TESTS = {
    'iris-debug3': 'Its inactive slot was reused; restore and verify a separate UKI before retesting.',
    'iris-debug4': 'The obsolete trace slot was archived and removed; restore and verify it before retesting.',
    'iris-debug5': 'The retained-context candidate failed resume and exposed an IRQ imbalance.',
}
ENV_HEADER = b'# GRUB Environment Block\n'


def environment(values):
    data = ENV_HEADER + ''.join(f'{key}={value}\n' for key, value in values.items()).encode()
    if len(data) > 1024:
        raise ValueError('GRUB environment exceeds its fixed block size')
    return data + b'#'*(1024-len(data))


def test_config(original, boot_uuid, test='grub216'):
    header = 'set timeout=10\nset default=0\n'
    if test not in TESTS:
        raise ValueError('Unsupported one-shot test')
    if test in RETIRED_TESTS:
        raise ValueError('Retired one-shot test: ' + RETIRED_TESTS[test])
    entry_id,label,loader = TESTS[test]
    if not original.startswith(header) or any(value[0] in original for value in TESTS.values()):
        raise ValueError('Unexpected/already staged GRUB menu; review it before testing')
    for name in ('arch-linux-nabu-debug.efi', 'arch-linux-nabu-stock.efi'):
        if f'chainloader /EFI/arch/{name}' not in original:
            raise ValueError('Working debug and stock entries must remain present')
    once = (f'search --no-floppy --fs-uuid --set=nabu_test_boot {boot_uuid}\n'
            'if [ -f ($nabu_test_boot)/grub2/grub-test.env ]; then\n'
            '  load_env -f ($nabu_test_boot)/grub2/grub-test.env next_entry\n'
            f'  if [ "$next_entry" = "{entry_id}" ]; then\n'
            '    set next_entry=\n'
            '    if save_env -f ($nabu_test_boot)/grub2/grub-test.env next_entry; then\n'
            f'      set default="{entry_id}"\n'
            '      set timeout=2\n'
            '    fi\n'
            '  fi\n'
            'fi\n')
    entry = (f"\nmenuentry '{label}' --id {entry_id} {{\n"
             f'  search --no-floppy --fs-uuid --set=root {boot_uuid}\n'
             f'  chainloader {loader}\n'
             '}\n')
    return header + once + original[len(header):] + entry


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--test', choices=tuple(name for name in TESTS if name not in RETIRED_TESTS), default='grub216')
    args = parser.parse_args()
    try:
        layout = load_layout()
        config = test_config(args.config.read_text(), layout['boot_filesystem_uuid'], args.test)
        relative = project_relative(args.output)
        if len(relative.parts) < 2 or relative.parts[0] != 'out':
            raise ValueError('Test bundle must be below out/')
        output = PROJECT/relative
        output.mkdir(parents=True, exist_ok=False)
        (output/'grub.cfg').write_text(config)
        (output/'grub-test.env').write_bytes(environment({'next_entry': TESTS[args.test][0]}))
        (output/'grub-candidate.env').write_bytes(environment({}))
        print('One-shot menu/environment bundle prepared. Nothing staged or rebooted.')
    except (OSError, ValueError) as error:
        raise SystemExit('GRUB test preparation refused: '+str(error))
