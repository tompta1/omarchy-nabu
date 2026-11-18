#!/usr/bin/env python3
"""Prepare a pinned Nabu tree, including reviewed stable-update resolutions."""
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import subprocess
from urllib.request import urlopen

PROJECT = Path(__file__).resolve().parents[1]


def contained(project, value):
    path = (project / value).resolve()
    if not path.is_relative_to(project.resolve()):
        raise ValueError('Kernel input/source path leaves the project')
    return path


def checked(data, expected):
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Kernel patch checksum mismatch')
    return data


def git(source, *args, check=True):
    return subprocess.run(['git', '-c', f'safe.directory={source}', '-C', str(source), *args],
                          check=check, capture_output=True, text=True)


def verify_prepared(source, profile):
    if git(source, 'rev-parse', 'HEAD').stdout.strip() != profile['commit']:
        return False
    if git(source, 'ls-files', '--others', '--exclude-standard').stdout.strip():
        return False
    if git(source, 'diff', '--quiet', check=False).returncode:
        return False
    tree = git(source, 'write-tree', check=False)
    return tree.returncode == 0 and tree.stdout.strip() == profile.get('prepared_tree')


def prepare(project, profile_path, output):
    profile = json.loads(profile_path.read_text())
    if not re.fullmatch(r'[0-9a-f]{40}', profile['commit']):
        raise ValueError('Kernel base must use a full commit pin')
    source = contained(project, profile.get('source_directory', 'upstream/linux-nabu'))
    if not source.is_relative_to((project/'upstream').resolve()):
        raise ValueError('Kernel source must be under upstream/')
    source.parent.mkdir(parents=True, exist_ok=True)
    if not (source/'.git').exists():
        cache = project/'upstream/linux-nabu'
        if cache != source and (cache/'.git').exists():
            subprocess.run(['git', '-c', f'safe.directory={cache}', 'clone', '--local',
                            '--no-checkout', str(cache), str(source)], check=True)
            git(source, 'remote', 'set-url', 'origin', profile['repository'])
            git(source, 'checkout', '--detach', profile['commit'])
        else:
            source.mkdir(exist_ok=True)
            git(source, 'init')
            git(source, 'remote', 'add', 'origin', profile['repository'])
    dirty = bool(git(source, 'status', '--porcelain').stdout)
    prepared = dirty and verify_prepared(source, profile)
    if dirty and not prepared:
        raise ValueError('Kernel source has modifications outside the reviewed prepared tree')
    inputs = output/'inputs'
    inputs.mkdir(parents=True, exist_ok=True)
    stable = profile.get('stable_patch')
    update = profile.get('upstream_update')
    if stable and update:
        raise ValueError('Select only one kernel update method')
    resolution = profile.get('resolution_patch')
    extra_patches = profile.get('patches', [])
    if not isinstance(extra_patches, list):
        raise ValueError('Kernel patches must be an ordered list')
    if extra_patches and not re.fullmatch(r'[0-9a-f]{40}', profile.get('prepared_tree', '')):
        raise ValueError('Additional kernel patches require a pinned resulting tree')
    reviewed_patches = []
    for index, entry in enumerate(extra_patches):
        local = contained(project, entry['path'])
        data = checked(local.read_bytes(), entry['sha256'])
        retained = inputs/f'{index:02d}-{local.name}'
        retained.write_bytes(data)
        reviewed_patches.append(retained)
    patch = None
    if stable:
        if not profile.get('prepared_tree') or not resolution or not stable['url'].startswith('https://'):
            raise ValueError('A stable update requires a pinned resulting tree and resolutions')
        archive = inputs/Path(stable['url']).name
        if not archive.exists():
            with urlopen(stable['url'], timeout=90) as response:
                archive.write_bytes(response.read())
        data = checked(archive.read_bytes(), stable['sha256'])
        patch = inputs/'stable.patch'
        patch.write_bytes(lzma.decompress(data) if archive.suffix == '.xz' else data)
        local_patch = contained(project, resolution['path'])
        checked(local_patch.read_bytes(), resolution['sha256'])
    if update:
        if not profile.get('prepared_tree') or not resolution:
            raise ValueError('Git update requires a pinned resulting tree and resolution')
        for key in ('base', 'commit'):
            if not re.fullmatch(r'[0-9a-f]{40}', update[key]):
                raise ValueError('Upstream update must use full commit pins')
        if not update['repository'].startswith('https://'):
            raise ValueError('Upstream update must use HTTPS')
        for key in ('base', 'commit'):
            git(source, 'fetch', '--depth=1', update['repository'], update[key])
        patch = inputs/'upstream.patch'
        patch.write_text(git(source, 'diff', '--binary', update['base'], update['commit']).stdout)
        local_patch = contained(project, resolution['path'])
        checked(local_patch.read_bytes(), resolution['sha256'])
    if not prepared:
        git(source, 'fetch', '--depth=1', 'origin', profile['commit'])
        git(source, 'checkout', '--detach', profile['commit'])
        if stable or update:
            git(source, 'apply', '--index', *['--exclude='+p for p in (stable or update)['exclude']], str(patch))
            git(source, 'apply', '--index', str(local_patch))
        for reviewed_patch in reviewed_patches:
            git(source, 'apply', '--index', str(reviewed_patch))
        if stable or update or reviewed_patches:
            if not verify_prepared(source, profile):
                raise ValueError('Applied kernel tree differs from the reviewed tree')
    receipt = {'profile': str(profile_path.relative_to(project)),
               'profile_sha256': hashlib.sha256(profile_path.read_bytes()).hexdigest(),
               'base_commit': profile['commit'],
               'tree': git(source, 'write-tree').stdout.strip()}
    (output/'kernel-source-receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print('Prepared kernel tree: '+receipt['tree'])
    return source


if __name__ == '__main__':
    from nabu_paths import kernel_output
    profile_path = contained(PROJECT, os.environ.get('NABU_KERNEL_PROFILE', 'kernel/sources.json'))
    try:
        prepare(PROJECT, profile_path, kernel_output())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'Kernel preparation refused: {error}')
