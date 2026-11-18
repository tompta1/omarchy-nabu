#!/usr/bin/env python3
"""Read the measured, untracked layout; a published example is never a target."""
import json
import os
from pathlib import Path
import sys
import uuid

PROJECT = Path(__file__).resolve().parent.parent


def load_layout():
    path = Path(os.environ.get('NABU_LAYOUT', 'local/tablet-layout.json'))
    if not path.is_absolute():
        path = PROJECT / path
    try:
        data = json.loads(path.read_text())
        profile = data.get('layout_profile', 'existing-cust')
        if profile not in ('existing-cust', 'tail-boot-v1'):
            raise ValueError('Unsupported layout profile')
        boot = 'nabu_boot' if profile == 'tail-boot-v1' else 'cust'
        for key, value in (('root_partition', 'userdata'), ('boot_partition', boot),
                           ('esp_partition', 'rawdump')):
            if data[key] != value:
                raise ValueError(f'{key} must be {value}; other layouts are not implemented')
        for kind in ('root', 'boot', 'esp'):
            size = data[f'{kind}_partition_bytes']
            if type(size) is not int or size <= 0:
                raise ValueError(f'{kind}_partition_bytes must be a positive integer')
            value = data[f'{kind}_filesystem_uuid']
            boot_type = data.get('boot_filesystem_type', 'ext4')
            if boot_type not in ('ext4', 'vfat'):
                raise ValueError('Unsupported boot filesystem type')
            if kind == 'esp' or kind == 'boot' and boot_type == 'vfat':
                import re
                if not re.fullmatch(r'[0-9a-fA-F]{4}-[0-9a-fA-F]{4}', value):
                    raise ValueError('Invalid FAT ESP UUID')
            else:
                uuid.UUID(value)
        if profile == 'existing-cust':
            if data['boot_partition_bytes'] != 1073741824:
                raise ValueError('Only the measured 1 GiB cust boot layout is implemented')
        else:
            if (data.get('layout_status') != 'proposed' or data.get('boot_filesystem_type') != 'vfat'
                    or data['boot_partition_bytes'] != 4*1024**3):
                raise ValueError('Tail-boot is an offline 4 GiB FAT proposal only')
            first, boot_start, original = (data.get(key) for key in
                ('root_start_lba', 'boot_start_lba', 'original_root_partition_bytes'))
            if (any(type(value) is not int or value <= 0 for value in (first, boot_start, original))
                    or boot_start % 256 or boot_start <= first
                    or data['root_partition_bytes'] != (boot_start-first)*4096
                    or not 4*1024**3 <= original-data['root_partition_bytes'] < 4*1024**3+1024**2):
                raise ValueError('Tail-boot proposal geometry is inconsistent')
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(f'Invalid or missing Nabu layout at {path}: {error}. '
                         'Copy profiles/nabu/tablet-layout.example.json to local/, '
                         'then fill it from the target device inventory.') from error
    return data


def require_existing_flash_layout(data):
    if (data.get('layout_profile', 'existing-cust') != 'existing-cust'
            or data.get('layout_status') == 'proposed'
            or data.get('boot_partition', 'cust') != 'cust'):
        raise ValueError('Proposed partition layouts are offline-only; migration flashing is not implemented')


if __name__ == '__main__':
    layout = load_layout()
    if sys.argv[1:] == ['--flash-check']:
        try:
            require_existing_flash_layout(layout)
        except ValueError as error:
            raise SystemExit(str(error))
    elif len(sys.argv) == 2:
        print(layout[sys.argv[1]])
    else:
        print(json.dumps(layout, indent=2))
