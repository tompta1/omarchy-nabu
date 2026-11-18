#!/usr/bin/env python3
"""Validate explicit user-local provisioning without printing credentials."""
import base64
import json
import os
from pathlib import Path
import re

PROJECT = Path(__file__).resolve().parent.parent


def local_file(value):
    path = Path(value)
    return path if path.is_absolute() else PROJECT / path


def load_provision():
    path = local_file(os.environ.get('NABU_PROVISION', 'local/provision.json'))
    try:
        data = json.loads(path.read_text())
        username = data['username']
        if not isinstance(username, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,30}', username):
            raise ValueError('username must be a simple Unix account name')
        if username in ('root', 'alarm', 'alpm', 'kernelbuild'):
            raise ValueError('reserved installation username')
        autologin = data.get('autologin', False)
        if type(autologin) is not bool:
            raise ValueError('autologin must be an explicit boolean')
        password = local_file(data['password_hash_file']).read_text().strip()
        # Accept crypt(3) SHA-512 or yescrypt hashes, never plaintext or a lock marker.
        sha512 = r'\$6\$(?:rounds=[1-9][0-9]{3,8}\$)?[A-Za-z0-9./]{1,16}\$[A-Za-z0-9./]{86}'
        yescrypt = r'\$y\$[A-Za-z0-9./]+\$[A-Za-z0-9./]{1,86}\$[A-Za-z0-9./]{43}'
        if not re.fullmatch(sha512+'|'+yescrypt, password):
            raise ValueError('password_hash_file must contain a SHA-512/yescrypt crypt hash')
        keys = []
        if data.get('authorized_keys_file') is not None:
            for line in local_file(data['authorized_keys_file']).read_text().splitlines():
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                fields = line.split()
                if len(fields) < 2 or not re.fullmatch(r'(?:ssh-(?:ed25519|rsa)|ecdsa-sha2-nistp(?:256|384|521))', fields[0]):
                    raise ValueError('authorized_keys_file must contain plain public keys, without options')
                if len(base64.b64decode(fields[1], validate=True)) < 32:
                    raise ValueError('invalid public key encoding')
                keys.append(line.strip())
        return {'username': username, 'password_hash': password,
                'authorized_keys': keys, 'autologin': autologin}
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Do not include exception details: JSON/input parse errors may quote secrets.
        raise SystemExit(f'Invalid provisioning at {path}. Use profiles/nabu/provision.example.json '
                         'with a local username, crypt password-hash file, optional public keys, '
                         'and boolean autologin. No account was provisioned.') from error


if __name__ == '__main__':
    config = load_provision()
    print(json.dumps({'username': config['username'], 'autologin': config['autologin'],
                      'ssh_public_keys': len(config['authorized_keys']),
                      'password': 'local crypt hash supplied'}, indent=2))
