#!/usr/bin/env python3
"""Offline source checks; never build, access a tablet, or execute repair scripts."""
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = {'.git', '.agents', '.codex', '.aws', 'local', 'out', 'upstream', '__pycache__'}
NESTED_EXCLUDED = {'.git', '.agents', '.codex', '.aws', '__pycache__'}
errors = []
counts = {'python': 0, 'json': 0, 'bash': 0, 'markdown': 0}

# Ignoring a directory does not untrack files previously added with git -f.
# CI must check the index as well as scanning the intended source filesystem.
if (ROOT/'.git').exists():
    tracked = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'],
                             capture_output=True, text=True)
    if tracked.returncode:
        errors.append('Cannot inspect tracked files for the source-only boundary')
    else:
        for name in tracked.stdout.split('\0'):
            if not name:
                continue
            parts = Path(name).parts
            if (parts[0] in EXCLUDED or any(part in NESTED_EXCLUDED for part in parts)
                    or any(part.startswith('.env') and part != '.env.example' for part in parts)):
                errors.append(f'{name}: private/output path is tracked in Git')


def candidates(directory):
    for path in sorted(directory.iterdir()):
        if (path.name in NESTED_EXCLUDED or directory == ROOT and path.name in EXCLUDED
                or path.name.startswith('.env') or path.is_symlink()):
            continue
        if path.is_dir():
            yield from candidates(path)
        elif path.is_file():
            yield path


for path in candidates(ROOT):
    relative = path.relative_to(ROOT)
    if (path.name in ('authorized_keys', 'known_hosts', 'shadow')
            or re.fullmatch(r'(?:id_(?:rsa|ed25519|ecdsa)(?:\.pub)?|ssh_host_.*_key(?:\.pub)?)', path.name)):
        errors.append(f'{relative}: credential/device record belongs under ignored local/, not source')
        continue
    if path.suffix in ('.log', '.pcap', '.dmp'):
        errors.append(f'{relative}: raw logs/evidence belong under ignored out/, not source')
        continue
    if (path.suffix in ('.pyc', '.img', '.efi', '.core', '.mbn', '.fw', '.rom', '.bin', '.rpm', '.deb', '.o', '.a', '.so')
            or re.search(r'\.pkg\.tar\.[A-Za-z0-9]+$', path.name)):
        errors.append(f'{relative}: binary/firmware artifact belongs under ignored out/, not source')
        continue
    raw = path.read_bytes()
    if raw.startswith((b'\x7fELF', b'MZ')) or b'\0' in raw:
        errors.append(f'{relative}: binary content in the source-only tree')
        continue
    try:
        text = raw.decode()
    except UnicodeError:
        errors.append(f'{relative}: binary content (non-UTF-8) in the source-only tree')
        continue
    try:
        first_line = text.splitlines()[0] if text else ''
        if path.suffix == '.py' or (first_line.startswith('#!') and 'python' in first_line):
            ast.parse(text, filename=str(relative))
            counts['python'] += 1
        elif path.suffix == '.sh' or path.name == 'PKGBUILD' or first_line in ('#!/bin/bash', '#!/bin/sh'):
            subprocess.run(['bash', '-n', str(path)], check=True, capture_output=True, text=True)
            counts['bash'] += 1
        if path.suffix == '.json':
            json.loads(text)
            counts['json'] += 1
        if path.suffix == '.md':
            counts['markdown'] += 1
            for target in re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)', text):
                if target.startswith(('#', 'https://', 'http://', 'mailto:')):
                    continue
                target = unquote(target.split('#', 1)[0].strip('<>'))
                destination = (path.parent / target).resolve()
                if not destination.is_relative_to(ROOT):
                    errors.append(f'{relative}: link leaves repository: {target}')
                elif any(part in EXCLUDED for part in destination.relative_to(ROOT).parts):
                    errors.append(f'{relative}: public link points to ignored content: {target}')
                elif not destination.exists():
                    errors.append(f'{relative}: missing link target: {target}')
        if path != Path(__file__).resolve():
            if re.search(r'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----', text):
                errors.append(f'{relative}: private key material in source')
            if re.search(r"printf[^\n]*123456[^\n]*\|[^\n]*sudo", text):
                errors.append(f'{relative}: embedded prototype sudo password')
            if re.search(r'/(?:var/)?home/[^/\s]+/(?:Projects|Documents)(?:/|$)|192\.168\.\d{1,3}\.\d{1,3}\b', text):
                errors.append(f'{relative}: lab-specific host path or address')
    except (SyntaxError, ValueError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        errors.append(f'{relative}: {detail}')

if errors:
    print('\n'.join(errors), file=sys.stderr)
    raise SystemExit(1)
print('Source checks passed: ' + ', '.join(f'{value} {key}' for key, value in counts.items()))
print('No image build, remote access, reboot, or flash was performed.')
