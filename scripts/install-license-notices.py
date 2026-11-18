#!/usr/bin/env python3
"""REL-03: install exact source notices in a marked image-build root."""
import argparse
from pathlib import Path
from nabu_notices import install

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'))
    args = parser.parse_args()
    try:
        install(args.root)
    except (OSError, ValueError) as exc:
        raise SystemExit(f'Notice installation refused: {exc}')
    print('Known source notices installed. Firmware and whole-image review remain open.')
