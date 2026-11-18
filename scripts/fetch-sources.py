#!/usr/bin/env python3
"""Fetch pinned build and device-reference revisions; run through host Podman."""
import argparse
from pathlib import Path
import subprocess
from nabu_sources import load_sources,scoped_sources

project = Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--scope',choices=('os','bootloader','all'),default='os')
args=parser.parse_args()
for name, source in scoped_sources(load_sources(project/'sources.json'),args.scope).items():
  target = project / 'upstream' / name
  if not (target / '.git').exists():
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', '-C', str(target), 'init'], check=True)
    subprocess.run(['git', '-C', str(target), 'remote', 'add', 'origin', source['url']], check=True)
  dirty = subprocess.check_output(['git', '-c', f'safe.directory={target}', '-C', str(target), 'status', '--porcelain'], text=True)
  if dirty:
    raise SystemExit(f'Refusing to change modified source tree: {target}')
  subprocess.run(['git', '-c', f'safe.directory={target}', '-C', str(target), 'fetch', '--depth=1', 'origin', source['commit']], check=True)
  subprocess.run(['git', '-c', f'safe.directory={target}', '-C', str(target), 'checkout', '--detach', source['commit']], check=True)
