#!/usr/bin/env python3
"""Inventory installed package license declarations/notices (REL-03), read-only.

Metadata and notice presence are evidence for review, not redistribution approval.
No credentials, device identities or arbitrary home files are read or exported.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


def fields(text):
    result = {}
    key = None
    for line in text.splitlines():
        if re.fullmatch(r'%[A-Z0-9_]+%', line):
            key = line.strip('%')
            if key in result:
                raise ValueError('Duplicate package metadata field')
            result[key] = []
        elif line and key is not None:
            result[key].append(line)
    return result


def inside(root, path):
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError('Package evidence path leaves supplied root')
    return resolved


def audit(root):
    root = root.resolve(strict=True)
    database = root/'var/lib/pacman/local'
    packages = []
    for directory in sorted(database.iterdir()):
        if not directory.is_dir():
            continue
        data = fields(inside(root, directory/'desc').read_text())
        if len(data.get('NAME', [])) != 1 or len(data.get('VERSION', [])) != 1:
            raise ValueError('Missing/ambiguous installed package identity')
        name, version = data['NAME'][0], data['VERSION'][0]
        if not re.fullmatch(r'[A-Za-z0-9@+_.-]+', name):
            raise ValueError('Unsafe installed package name')
        licenses = data.get('LICENSE', [])
        notices = []
        for value in fields(inside(root, directory/'files').read_text()).get('FILES', []):
            if not value.startswith('usr/share/licenses/') or value.endswith('/'):
                continue
            path = PurePosixPath(value)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Unsafe installed license path')
            target = root/value
            if not target.is_file():
                continue
            target = inside(root, target)
            notices.append({'path': value, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                            'bytes': target.stat().st_size})
        flags = []
        if not licenses:
            flags.append('missing-declaration')
        if any(re.search(r'custom|licenseref|proprietary|unknown', value, re.I) for value in licenses):
            flags.append('needs-individual-license-review')
        if any(re.search(r'\b(?:A?GPL|LGPL)', value, re.I) for value in licenses):
            flags.append('copyleft-source-review')
        packages.append({'name': name, 'version': version, 'declared_licenses': licenses,
                         'upstream_urls': data.get('URL', []), 'notice_files': notices,
                         'review_flags': flags})
    if not packages:
        raise ValueError('No installed package metadata found')
    return {'schema': 1, 'scope': 'Installed package declarations and license-file hashes only',
            'redistribution_approved': False,
            'summary': {'packages': len(packages),
                        'missing_declarations': sum('missing-declaration' in p['review_flags'] for p in packages),
                        'individual_license_reviews': sum('needs-individual-license-review' in p['review_flags'] for p in packages),
                        'copyleft_source_reviews': sum('copyleft-source-review' in p['review_flags'] for p in packages)},
            'packages': packages}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.root)
    with args.output.open('x') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps(report['summary'], indent=2))
    print('Inventory only; source/notice completeness and redistribution decisions remain review work.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as exc:
        raise SystemExit(f'Package license inventory failed: {exc}')
