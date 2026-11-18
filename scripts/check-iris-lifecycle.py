#!/usr/bin/env python3
"""HW-08: compile actual Iris lifecycle/PM code against fault-injection mocks.

This checks control flow and reference accounting, not firmware or hardware.
Requires a prepared candidate kernel tree and a native C compiler.
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    match = re.search(r'^(?:static )?(?:int|void) ' + re.escape(name) + r'\([^;]+?\)\n\{', source, re.M)
    if not match:
        raise ValueError(f'Missing function: {name}')
    start = match.start()
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


def check(source, compiler, core_only=False):
    iris = source / 'drivers/media/platform/qcom/iris'
    core = re.sub(r'^#include[^\n]*\n', '', (iris/'iris_core.c').read_text(), flags=re.M)
    pm = ''
    if not core_only:
        vpu = (iris/'iris_vpu_common.c').read_text()
        pm = '\n'.join(function(vpu, name) for name in ('iris_vpu5_suspend', 'iris_vpu5_resume'))
    with tempfile.TemporaryDirectory(prefix='iris-lifecycle-') as directory:
        work = Path(directory)
        (work/'iris_core_under_test.inc').write_text(core)
        (work/'iris_pm_under_test.inc').write_text(pm)
        binary = work/'check'
        subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-function', *(['-DCORE_ONLY'] if core_only else []), '-I', str(work),
                        str(ROOT/'tests/c/iris-lifecycle.c'), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
        if not core_only:
            power = '\n'.join(function(vpu, name) for name in (
                'iris_vpu_power_on', 'iris_vpu_power_off_resources',
                'iris_vpu_power_off', 'iris_vpu5_pm_power_off'))
            (work/'iris_power_under_test.inc').write_text(power)
            subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(work), str(ROOT/'tests/c/iris-power.c'),
                            '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
            resources = (iris/'iris_resources.c').read_text()
            (work/'iris_opp_under_test.inc').write_text(function(resources, 'iris_opp_set_rate'))
            subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(work), str(ROOT/'tests/c/iris-opp.c'),
                            '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--cc', default='cc')
    parser.add_argument('--core-only', action='store_true', help='Also check historical cores without PM helpers')
    args = parser.parse_args()
    check(args.source.resolve(), args.cc, args.core_only)
