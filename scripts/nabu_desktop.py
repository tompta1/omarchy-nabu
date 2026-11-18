#!/usr/bin/env python3
"""OS-10: converge optional desktop recipes inside a marked image-build root."""
import argparse
import hashlib
from pathlib import Path
import shutil
from nabu_image_options import PROJECT, INSTALLED, encoded, load_options, validate
from nabu_screensaver import apply_screensaver

OPTIONAL_FILES = {
    'usr/local/bin/omarchy-nabu-display-install': 'scripts/omarchy-nabu-display-install',
    'usr/local/bin/omarchy-nabu-display-control': 'scripts/omarchy-nabu-display-control',
    'usr/local/share/omarchy-nabu/display-controls/NabuControls.qml': 'desktop/display-controls/NabuControls.qml',
    'usr/local/share/applications/omarchy-nabu-display-controls.desktop': 'desktop/display-controls/launcher.desktop',
    'etc/systemd/user/omarchy-nabu-display-setup.service': 'desktop/display-controls/setup.service',
}
SCREENSAVER_FILES = {
    'usr/local/libexec/omarchy-nabu-screensaver-touch': 'scripts/nabu_screensaver.py',
    'etc/pacman.d/hooks/96-omarchy-nabu-screensaver-touch.hook': 'desktop/screensaver-touch/96-omarchy-nabu-screensaver-touch.hook',
}
SETUP_LINK = 'etc/systemd/user/graphical-session.target.wants/omarchy-nabu-display-setup.service'
# Reviewed earlier helpers, before notice/first-login and terminology changes. Permit
# removal of these exact shipped bytes, never arbitrary edits in a reused root.
LEGACY_HASHES = {
    'usr/local/bin/omarchy-nabu-display-install': {
        'a406d32cbb6cc22e6c19c99c870963759984c1d224aee9b6037945fefbd143f6',
        '7b65618d208c9884bd62eac1f1f01d4bba78f5062b358141bd4c76d8df0154bc',
    },
    'usr/local/bin/omarchy-nabu-display-control': {'1b2c0d07af29d23c586cddd25a60f3c41f3ca649a6fb2f16bfef363f22ce9824'},
}


def checked(root, relative):
    path = root/relative
    if path.is_symlink() or not path.parent.resolve().is_relative_to(root):
        raise ValueError('Unexpected image-options path or symlink: '+relative)
    return path


def configure(root, options, project=PROJECT):
    root = root.resolve(strict=True)
    options = validate(options)
    if not (root/'.omarchy-nabu-build-root').is_file():
        raise ValueError('A marked image-build root is required')
    # Check every removal/overwrite before editing anything. Default assembly
    # cannot silently reuse a home with an enabled/customized user clone.
    if not options['display_controls'] and list((root/'home').glob('*/.config/omarchy/plugins/nabu.display')):
        raise ValueError('Existing user display clone requires review; use a clean prepared root')
    for relative, source in (OPTIONAL_FILES | SCREENSAVER_FILES).items():
        target = checked(root, relative)
        if target.exists():
            allowed = LEGACY_HASHES.get(relative, set()) | {hashlib.sha256((project/source).read_bytes()).hexdigest()}
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() not in allowed:
                raise ValueError('Unrecognized optional file; preserving it for review: '+relative)
    link = root/SETUP_LINK
    if not link.parent.resolve().is_relative_to(root):
        raise ValueError('Display setup link leaves build root')
    if link.is_symlink():
        if link.readlink() != Path('../omarchy-nabu-display-setup.service'):
            raise ValueError('Unrecognized display setup link')
    elif link.exists():
        raise ValueError('Display setup enablement is not the expected symlink')
    destination = checked(root, INSTALLED)
    # Restore only the exact earlier customization when returning to default.
    # Unknown upstream/user forms refuse assembly before feature files change.
    apply_screensaver(root, options['screensaver_touch_dismiss'])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encoded(options))
    destination.chmod(0o644)
    for relative, source in (OPTIONAL_FILES | SCREENSAVER_FILES).items():
        target = root/relative
        selected = options['display_controls'] if relative in OPTIONAL_FILES else options['screensaver_touch_dismiss']
        if selected:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(project/source, target)
            target.chmod(0o755 if relative.startswith(('usr/local/bin/', 'usr/local/libexec/')) else 0o644)
        else:
            target.unlink(missing_ok=True)
    link.unlink(missing_ok=True)
    if options['display_controls']:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to('../omarchy-nabu-display-setup.service')
    # The always-installed helper applies only required rotation workarounds.
    # The screensaver recipe and its separate upgrade hook are opt-in files.
    for source, relative in (
        ('scripts/nabu_image_options.py', 'usr/local/libexec/nabu_image_options.py'),
        ('scripts/omarchy-nabu-shell-compat', 'usr/local/libexec/omarchy-nabu-shell-compat'),
    ):
        target = checked(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project/source, target)
        target.chmod(0o755 if relative.endswith('shell-compat') else 0o644)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'))
    args = parser.parse_args()
    try:
        configure(args.root, load_options())
    except (OSError, ValueError) as exc:
        raise SystemExit(f'Desktop option configuration refused: {exc}')
