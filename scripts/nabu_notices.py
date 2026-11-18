"""Install/check the known source notices; this is not a whole-image clearance."""
import hashlib
from pathlib import Path
import json

PROJECT = Path(__file__).resolve().parents[1]
DESTINATION = 'usr/share/licenses/omarchy-nabu'
MANIFEST = 'usr/share/omarchy-nabu/notices.json'
NOTICES = {
    'PROJECT-LICENSE': 'LICENSE',
    'PROJECT-NOTICE': 'NOTICE',
    'omarchy-MIT.txt': 'licenses/omarchy-MIT.txt',
    'nabu-MIT.txt': 'licenses/nabu-MIT.txt',
    'nabu-pkgs-MIT.txt': 'licenses/nabu-pkgs-MIT.txt',
    'GPL-2.0-only.txt': 'licenses/GPL-2.0-only.txt',
    'GPL-3.0.txt': 'licenses/GPL-3.0.txt',
    'wvkbd-COPYING.txt': 'licenses/wvkbd-COPYING.txt',
    'wvkbd-COPYING-WESTON.txt': 'licenses/wvkbd-COPYING-WESTON.txt',
}


def manifest(project=PROJECT):
    return {'schema': 1, 'whole_image_redistribution_approved': False,
            'notices': [{'path': DESTINATION+'/'+name,
                         'sha256': hashlib.sha256((project/source).read_bytes()).hexdigest()}
                        for name, source in NOTICES.items()]}


def install(root, project=PROJECT):
    root = root.resolve(strict=True)
    if not (root/'.omarchy-nabu-build-root').is_file():
        raise ValueError('A marked image-build root is required')
    for relative in (DESTINATION, str(Path(MANIFEST).parent)):
        path = root/relative
        if not path.resolve().is_relative_to(root):
            raise ValueError('Notice destination leaves build root')
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o755)
    for name, source in NOTICES.items():
        target = root/DESTINATION/name
        if target.is_symlink():
            raise ValueError('Unexpected notice symlink')
        target.write_bytes((project/source).read_bytes())
        target.chmod(0o644)
    target = root/MANIFEST
    if target.is_symlink():
        raise ValueError('Unexpected notice manifest symlink')
    target.write_text(json.dumps(manifest(project), indent=2)+'\n')
    target.chmod(0o644)
