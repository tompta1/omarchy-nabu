#!/usr/bin/env python3
"""Validate and copy receipts for a prepared root reused with another kernel."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from nabu_paths import PROJECT, output_dir, project_relative
from nabu_sources import load_sources,scoped_sources
from nabu_image_options import validate as validate_options, digest as options_digest
from nabu_repositories import load_policy


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reuse(source, output):
    if source.resolve() == output.resolve():
        raise ValueError('Source and destination runs must differ')
    volume = (source/'work-volume.txt').read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', volume):
        raise ValueError('Invalid prepared-root volume')
    parameters = json.loads((source/'build-parameters.json').read_text())
    for name in ('NABU_LAYOUT', 'NABU_PROVISION'):
        current = PROJECT/project_relative(os.environ[name])
        if parameters[name+'_sha256'] != digest(current):
            raise ValueError('Prepared root uses different layout/provisioning inputs')
    if scoped_sources(load_sources(source/'sources.json')) != scoped_sources(load_sources(PROJECT/'sources.json')):
        raise ValueError('Prepared root uses different upstream source pins')
    for package in load_policy(PROJECT/'profiles/nabu/repositories.json')['source_packages']:
        previous = source/'device-packages'/(package+'.PKGBUILD')
        current = PROJECT/'packages'/package/'PKGBUILD'
        if not previous.is_file() or digest(previous) != digest(current):
            raise ValueError('Prepared root uses a different package recipe: '+package)
    source_options = None
    if (source/'image-options.json').exists():
        source_options = validate_options(json.loads((source/'image-options.json').read_text()))
        if parameters.get('image_options_sha256') != options_digest(source_options):
            raise ValueError('Prepared-root option selection differs from its build receipt')
    elif 'image_options_sha256' in parameters:
        raise ValueError('Prepared-root option receipt is missing')
    for name in ('packages.lock','sources.json','bootstrap.sha256','keyboard-source.json','desktop-smoke.log'):
        shutil.copy2(source/name, output/name)
    shutil.copytree(source/'device-packages',output/'device-packages')
    (output/'root-provenance.json').write_text(json.dumps({
        'source_run':str(source.relative_to(PROJECT)),
        'sources_sha256':digest(source/'sources.json'),
        'packages_sha256':digest(source/'packages.lock'),
        'source_image_options':source_options,
        'desktop_reconfiguration_required':True,
        'purpose':'prepared OS base; current device integration and independently selected kernel/bootloader',
        'separate_bootloader_inputs':scoped_sources(load_sources(PROJECT/'sources.json'),'bootloader')},indent=2)+'\n')
    return volume


if __name__=='__main__':
    try:
        relative=project_relative(os.environ['NABU_SOURCE_RUN'])
        if not relative.parts or relative.parts[0]!='out':
            raise ValueError('Prepared run must be under out/')
        print(reuse(PROJECT/relative,output_dir()))
    except (OSError,ValueError,KeyError) as error:
        raise SystemExit(f'Prepared-root reuse refused: {error}')
