"""OS-10: small explicit image preferences, independent of device/account inputs."""
import hashlib
import json
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
INSTALLED = 'etc/omarchy-nabu/image-options.json'
DEFAULT = {'schema': 1, 'display_controls': False,
           'screensaver_touch_dismiss': False, 'hide_kernel_logos': False}


def validate(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Image options require exactly schema and the three documented switches')
    if type(value['schema']) is not int or value['schema'] != 1:
        raise ValueError('Unsupported image-options schema')
    if any(type(value[key]) is not bool for key in DEFAULT if key != 'schema'):
        raise ValueError('Image-option switches must be booleans')
    return {key: value[key] for key in DEFAULT}


def load_options(path=None):
    path = Path(path or os.environ.get('NABU_IMAGE_OPTIONS', 'profiles/nabu/image-options.default.json'))
    if not path.is_absolute():
        path = PROJECT/path
    return validate(json.loads(path.read_text()))


def encoded(options):
    return (json.dumps(validate(options), sort_keys=True, indent=2)+'\n').encode()


def digest(options):
    return hashlib.sha256(encoded(options)).hexdigest()


def installed_options(root):
    path = root/INSTALLED
    return validate(json.loads(path.read_text())) if path.exists() else dict(DEFAULT)


def kernel_cmdline(base, options):
    options = validate(options)
    if 'logo.nologo' in base.split():
        raise ValueError('Common kernel arguments must not embed an optional logo preference')
    return base.rstrip()+(' logo.nologo' if options['hide_kernel_logos'] else '')+'\n'


if __name__ == '__main__':
    print(encoded(load_options()).decode(), end='')
