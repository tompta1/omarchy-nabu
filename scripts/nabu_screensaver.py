#!/usr/bin/env python3
"""Optional touch/click dismissal of the decorative Omarchy Foot screensaver."""
import argparse
from pathlib import Path
import shutil
from nabu_image_options import installed_options

FOOT_RELATIVE = 'usr/share/omarchy/default/foot/screensaver.ini'
FOOT_ORIGINAL = '''[main]
font=JetBrainsMono Nerd Font:size=18
pad=0x0

[colors-dark]
background=000000
foreground=ffffff
'''
FOOT_REPLACEMENT = FOOT_ORIGINAL + '''
# Omarchy Nabu: one touch or mouse click dismisses this decorative window.
[mouse-bindings]
selection-override-modifiers=none
select-begin=none
select-extend=none
quit=BTN_LEFT BTN_RIGHT
'''

def apply_screensaver(root, enabled=True):
    target = root/FOOT_RELATIVE
    if not target.exists():
        return 'No Foot screensaver config in this Omarchy version; no change'
    original = target.read_text()
    if not enabled:
        if original == FOOT_ORIGINAL:
            return 'Upstream Foot screensaver behavior retained'
        if original != FOOT_REPLACEMENT:
            raise ValueError('Unrecognized Foot screensaver config; preserving it for review')
        target.write_text(FOOT_ORIGINAL)
        return 'Restored upstream Foot screensaver behavior'
    if original == FOOT_REPLACEMENT:
        return 'Foot screensaver touch/mouse dismissal already configured'
    if original != FOOT_ORIGINAL:
        raise ValueError('Unrecognized Foot screensaver config; preserving it for review')
    backup = root/'var/lib/omarchy-nabu/compat/screensaver-foot.ini.original'
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        shutil.copy2(target, backup)
    target.write_text(FOOT_REPLACEMENT)
    return 'Configured Foot screensaver touch/mouse dismissal; next launch uses it'



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'))
    args = parser.parse_args()
    if not installed_options(args.root)['screensaver_touch_dismiss']:
        raise ValueError('Screensaver touch recipe is not selected for this image')
    print(apply_screensaver(args.root))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc))
