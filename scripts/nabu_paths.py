#!/usr/bin/env python3
"""Resolve build paths consistently on the host and in /project or /build."""
import os
import json
from pathlib import Path
import re
import sys

PROJECT = Path(__file__).resolve().parent.parent


def project_relative(value):
    path = Path(value)
    path = (path if path.is_absolute() else PROJECT/path).resolve()
    if not path.is_relative_to(PROJECT):
        raise ValueError('Build inputs and outputs must be inside the project bind mount')
    return path.relative_to(PROJECT)


def output_dir():
    relative = project_relative(os.environ.get('NABU_OUTPUT_REL', 'out'))
    if not relative.parts or relative.parts[0] != 'out':
        raise ValueError('Build output must be inside out/')
    return PROJECT/relative


def kernel_profile():
    path = PROJECT/project_relative(os.environ.get('NABU_KERNEL_PROFILE', 'kernel/sources.json'))
    return json.loads(path.read_text())


def kernel_output():
    value = os.environ.get('NABU_KERNEL_OUTPUT') or kernel_profile()['build']['output']
    relative = project_relative(value)
    if len(relative.parts) < 2 or relative.parts[0] != 'out':
        raise ValueError('Compiled kernel output must be below out/')
    return PROJECT/relative


def kernel_work_volume():
    value = os.environ.get('NABU_KERNEL_WORK_VOLUME') or kernel_profile()['build']['work_volume']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
        raise ValueError('Invalid kernel work volume name')
    return value


def kernel_input():
    value = os.environ.get('NABU_KERNEL_INPUT_REL')
    return PROJECT/project_relative(value) if value else kernel_output()


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['output']:
            print(output_dir())
        elif sys.argv[1:] == ['kernel']:
            print(kernel_input())
        elif sys.argv[1:] == ['kernel-output']:
            print(kernel_output())
        elif sys.argv[1:] == ['kernel-volume']:
            print(kernel_work_volume())
        elif len(sys.argv) == 3 and sys.argv[1] == 'relative':
            print(project_relative(sys.argv[2]))
        else:
            raise ValueError('Usage: nabu_paths.py output|kernel|kernel-output|kernel-volume|relative PATH')
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(str(error))
