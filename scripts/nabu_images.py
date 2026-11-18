#!/usr/bin/env python3
"""Bind install images to their measured layout and local build inputs."""
import argparse
import hashlib
import json
from pathlib import Path
from nabu_layout import load_layout
from nabu_paths import output_dir, kernel_input
from nabu_image_options import validate as validate_options, digest as options_digest
from nabu_kernel import select as select_kernel

ARTIFACTS = ('rootfs.img', 'boot.img')


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def layout_digest(layout):
    return hashlib.sha256(json.dumps(layout, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def create(output, layout):
    images = output/'images'
    record = {'schema': 1, 'project': 'Omarchy Quattro on Xiaomi Nabu',
              'board': 'nabu', 'boot_profile': 'existing-uboot',
              'layout': layout, 'layout_sha256': layout_digest(layout),
              'hardware_tested': False, 'redistribution_approved': False,
              'artifacts': {name: {'sha256': digest(images/name), 'bytes': (images/name).stat().st_size}
                            for name in ARTIFACTS}}
    for name in ('sources.json', 'packages.lock', 'bootstrap.sha256', 'build-parameters.json', 'image-options.json', 'recovery-packages.lock'):
        path = output/name
        if path.is_file(): record.setdefault('input_receipts', {})[name] = digest(path)
    if (output/'image-options.json').is_file():
        options = validate_options(json.loads((output/'image-options.json').read_text()))
        if (output/'build-parameters.json').is_file():
            parameters = json.loads((output/'build-parameters.json').read_text())
            if parameters.get('image_options_sha256') != options_digest(options):
                raise ValueError('Image options differ from build input receipt')
        record['image_options'] = options
        record['image_options_sha256'] = options_digest(options)
    kernel = kernel_input()
    if (kernel/'kernelrelease').is_file():
        record['kernel'] = {'release': (kernel/'kernelrelease').read_text().strip(),
                            'checksums_sha256': digest(kernel/'SHA256SUMS'),
                            'selection': select_kernel(kernel)}
    with (output/'image-receipt.json').open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    verify(output, layout)
    return record


def verify(output, layout):
    record = json.loads((output/'image-receipt.json').read_text())
    if (record.get('schema') != 1 or record.get('board') != 'nabu'
            or record.get('boot_profile') != 'existing-uboot'):
        raise ValueError('Unsupported image receipt')
    if record.get('layout') != layout or record.get('layout_sha256') != layout_digest(layout):
        raise ValueError('Images are bound to a different measured layout')
    if 'image_options' in record or 'image_options_sha256' in record:
        options = validate_options(record['image_options'])
        if options_digest(options) != record['image_options_sha256']:
            raise ValueError('Image options differ from receipt hash')
        if validate_options(json.loads((output/'image-options.json').read_text())) != options:
            raise ValueError('Image options differ from recorded selection')
    elif (output/'image-options.json').exists():
        raise ValueError('Image receipt omits the recorded options')
    artifacts = record.get('artifacts', {})
    if set(artifacts) != set(ARTIFACTS):
        raise ValueError('Receipt must contain exactly rootfs.img and boot.img')
    for name in ARTIFACTS:
        path = output/'images'/name
        expected = artifacts[name]
        if path.stat().st_size != expected['bytes'] or digest(path) != expected['sha256']:
            raise ValueError(f'Image differs from verified receipt: {name}')
    if artifacts['boot.img']['bytes'] != layout['boot_partition_bytes']:
        raise ValueError('Boot image differs from measured partition size')
    if artifacts['rootfs.img']['bytes'] > layout['root_partition_bytes']:
        raise ValueError('Root image exceeds measured partition size')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('create', 'verify'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        function = create if args.action == 'create' else verify
        function(args.output or output_dir(), load_layout())
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(f'Image receipt rejected: {error}')
    print('Image checksums, partition sizes and measured layout match the receipt.')
