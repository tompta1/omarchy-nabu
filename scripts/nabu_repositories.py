#!/usr/bin/env python3
"""Shared package-source policy for image audit, assembly and pacman refresh."""
import argparse
import json
from pathlib import Path
import re

SANDBOX_INCLUDE = 'Include = /etc/pacman.d/omarchy-nabu-sandbox.conf'


def load_policy(path):
    policy = json.loads(Path(path).read_text())
    names = [r['name'] for r in policy['repositories']]
    if (policy['schema'] != 1 or policy['architecture'] != 'aarch64'
            or policy['channel'] != 'stable'
            or names != ['omarchy', 'core', 'extra', 'alarm', 'nabu']):
        raise ValueError('Unsupported Nabu repository policy')
    return policy


def database_urls(policy):
    return [(r['name'], r['server'].replace('$arch', policy['architecture'])
             .replace('$repo', r['name']) + '/' + r['name'] + '.db')
            for r in policy['repositories']]


def apply_policy(config, policy):
    """Keep upstream/custom settings, put Nabu last, merge hardware holds."""
    lines = []
    section = None
    ignored = []
    for line in config.splitlines():
        match = re.fullmatch(r'\s*\[([^]]+)\]\s*(?:#.*)?', line)
        if match:
            section = match[1]
        if section == 'nabu' or re.fullmatch(
                r'\s*Include\s*=\s*/etc/pacman.d/nabu.conf\s*(?:#.*)?', line):
            continue
        if section == 'options' and re.match(r'\s*IgnorePkg\s*=', line):
            ignored.extend(line.split('=', 1)[1].split('#', 1)[0].split())
            continue
        if section == 'options' and line.strip() == SANDBOX_INCLUDE:
            continue
        # A refreshed upstream template may use a different channel.
        if section == 'omarchy' and re.match(r'\s*Server\s*=', line):
            line = 'Server = ' + policy['repositories'][0]['server']
        lines.append(line)
    if '[options]' not in [line.strip() for line in lines]:
        raise ValueError('pacman config has no options section')
    if '[omarchy]' not in [line.strip() for line in lines]:
        raise ValueError('pacman config has no Omarchy repository')
    holds = list(dict.fromkeys(ignored + policy['held_packages']))
    index = next(i for i, line in enumerate(lines) if line.strip() == '[options]')
    lines.insert(index + 1, 'IgnorePkg = ' + ' '.join(holds))
    lines.insert(index + 2, SANDBOX_INCLUDE)
    return '\n'.join(lines).rstrip() + '\n\nInclude = /etc/pacman.d/nabu.conf\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('config', type=Path)
    args = parser.parse_args()
    policy = load_policy(args.policy)
    updated = apply_policy(args.config.read_text(), policy)
    args.config.write_text(updated)
