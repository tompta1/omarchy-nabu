#!/usr/bin/env python3
"""OS-05: apply only supported Omarchy tunings on the Nabu board layer."""
import argparse
import gzip
import os
from pathlib import Path
import re
import stat
import tempfile

PREFIX = '# nabu: unavailable in this kernel: '
TUNINGS = {'net.core.default_qdisc': ('fq', 'CONFIG_NET_SCH_FQ'),
           'net.ipv4.tcp_congestion_control': ('bbr', 'CONFIG_TCP_CONG_BBR')}
IO_RULE = ('ACTION=="add|change", SUBSYSTEM=="block", ENV{DEVTYPE}=="disk", '
           'KERNEL=="nvme*|sd*|mmcblk*|vd*", ATTR{queue/scheduler}="kyber"')
UFS_GUARD = 'ACTION=="add|change", SUBSYSTEM=="block", DRIVERS=="ufshcd-qcom", GOTO="nabu_omarchy_io_end"'
UFS_RULE = UFS_GUARD+'\n'+IO_RULE+'\nLABEL="nabu_omarchy_io_end"'


def io_text(text):
    if UFS_GUARD in text or text.splitlines().count(IO_RULE) != 1:
        return text
    return text.replace(IO_RULE, UFS_RULE, 1)


def sysctl_text(text, config):
    enabled = set(re.findall(r'^(CONFIG_\w+)=[ym]$', config, re.M))
    result = []
    for line in text.splitlines(keepends=True):
        original = line.removeprefix(PREFIX)
        for key, (value, symbol) in TUNINGS.items():
            if re.fullmatch(r'\s*'+re.escape(key)+r'\s*=\s*'+value+r'\s*(?:#.*)?', original.strip()):
                line = original if symbol in enabled else PREFIX+original
                break
        result.append(line)
    return ''.join(result)


def update(path, transform):
    if path.is_symlink():
        raise ValueError('Refusing a symlink tuning file: '+path.name)
    if not path.exists():
        return False
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Tuning input is not a regular file: '+path.name)
    before = path.read_text()
    after = transform(before)
    if before == after:
        return False
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(after)
            stream.flush()
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            if os.geteuid() == 0:
                os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return True


def configure(root, config):
    changed = []
    for relative, transform in (
        ('etc/sysctl.d/99-omarchy-sysctl.conf', lambda text: sysctl_text(text, config)),
        ('etc/udev/rules.d/60-omarchy-io-scheduler.rules',
         io_text),
    ):
        path = root/relative
        if not path.parent.resolve().is_relative_to(root.resolve()):
            raise ValueError('Tuning path leaves the selected root')
        if update(path, transform):
            changed.append(relative)
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'))
    parser.add_argument('--kernel-config', type=Path, default=Path('/proc/config.gz'))
    args = parser.parse_args()
    data = args.kernel_config.read_bytes()
    config = (gzip.decompress(data) if data.startswith(b'\x1f\x8b') else data).decode()
    if 'CONFIG_ARM64=y' not in config.splitlines():
        raise ValueError('Expected the selected ARM64 kernel configuration')
    for path in configure(args.root, config):
        print('Adjusted Nabu compatibility: '+path)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit('Nabu tuning refused: '+str(error))
