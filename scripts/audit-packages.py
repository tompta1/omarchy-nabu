#!/usr/bin/env python3
"""Audit direct ARM package requests and repository precedence before assembly.

Pacman remains responsible for dependency/version solving inside the build root.
"""
import io
import json
from pathlib import Path
import subprocess
import tarfile
from urllib.request import urlopen
from nabu_paths import output_dir
from nabu_repositories import database_urls, load_policy
from nabu_sources import load_sources,scoped_sources

project = Path(__file__).resolve().parent.parent
policy = load_policy(project / 'profiles/nabu/repositories.json')
repositories = database_urls(policy)
available = {}
providers = {}
by_repository = {}
candidates = {}
invalid_records = []
for repo, url in repositories:
  print(f'Reading {repo}: {url}', flush=True)
  with urlopen(url, timeout=90) as response:
    data = response.read()
  if data.startswith(b'\x28\xb5\x2f\xfd'):
    try:
      from compression import zstd
    except ImportError:
      data = subprocess.check_output(['zstd', '-dc'], input=data)
    else:
      data = zstd.decompress(data)
  with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
    for member in archive:
      if not member.name.endswith('/desc'):
        continue
      fields = archive.extractfile(member).read().decode().split('\n\n')
      record = {}
      for field in fields:
        lines = field.strip().splitlines()
        if len(lines) > 1:
          record[lines[0].strip('%')] = lines[1:]
      if not all(record.get(key) for key in ('NAME', 'VERSION', 'FILENAME')):
        invalid_records.append({'repository': repo, 'member': member.name})
        continue
      name = record['NAME'][0]
      entry = {'repository': repo, 'version': record['VERSION'][0],
               'filename': record['FILENAME'][0],
               'sha256': record.get('SHA256SUM', [None])[0]}
      by_repository[repo, name] = entry
      candidates.setdefault(name, []).append(entry)
      available.setdefault(name, entry)
      for provided in record.get('PROVIDES', []):
        providers.setdefault(provided.split('=')[0], name)
plan = subprocess.check_output(['bash', str(project / 'scripts/package-plan.sh')], text=True).splitlines()
selected = {}
for request in sorted(set(plan)):
  if '/' in request:
    repo, name = request.split('/', 1)
    if policy['hardware_overrides'].get(name) != repo:
      raise SystemExit('Unreviewed repository override: ' + request)
    entry = by_repository.get((repo, name))
  else:
    name = request
    if name in policy['hardware_overrides']:
      raise SystemExit('Hardware request must qualify its repository: ' + name)
    entry = available.get(name)
    if entry is None and name in providers:
      entry = {**available[providers[name]], 'provider': providers[name]}
  if entry is not None:
    selected[request] = entry
missing = sorted(set(plan) - selected.keys())
for name in policy['normal_applications']:
  if name in available and available[name]['repository'] == 'nabu':
    raise SystemExit('Normal application unexpectedly requires the Nabu repository: ' + name)
if 'omarchy' in selected and selected['omarchy']['version'].rsplit('-', 1)[0] != policy['desktop_version']:
  raise SystemExit('Omarchy stable moved beyond the reviewed release; update source/policy together')
receipt = {'sources': scoped_sources(load_sources(project/'sources.json')),
           'channel': policy['channel'], 'repository_policy': policy,
           'scope': 'direct requests; pacman solves dependencies during assembly',
           'source_packages': {name: str(Path('packages') / name / 'PKGBUILD')
                               for name in policy['source_packages']},
           'packages': selected,
           'overlaps': {name: entries for name, entries in candidates.items() if len(entries) > 1},
           'invalid_records': invalid_records,
           'missing': missing}
output = output_dir() / 'package-audit.json'
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(receipt, indent=2) + '\n')
print(f'{len(set(plan))} requested packages; {len(missing)} unavailable')
if invalid_records:
  print('Warning: malformed repository entries excluded: ' + json.dumps(invalid_records))
if missing:
  raise SystemExit('Unavailable ARM64 packages: ' + ', '.join(missing))
