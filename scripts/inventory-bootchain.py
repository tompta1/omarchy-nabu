#!/usr/bin/env python3
"""Inventory built artifacts and packaged firmware; never access a tablet."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import struct
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True, help='read-only assembled root directory')
args = parser.parse_args()
project = Path(__file__).resolve().parent.parent
root = args.root
output = project / 'out/inventory'
output.mkdir(parents=True, exist_ok=True)

def record(path, base):
  entry = {'path': str(path.relative_to(base))}
  mode = path.lstat().st_mode
  if stat.S_ISLNK(mode):
    entry['symlink'] = str(path.readlink())
    return entry
  if not stat.S_ISREG(mode):
    return entry
  entry['bytes'] = path.stat().st_size
  with path.open('rb') as stream:
    entry['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
  with path.open('rb') as stream:
    header = stream.read(512)
  if header[:8] == b'ANDROID!':
    entry['format'] = 'Android boot image'
    entry['kernel_bytes'] = struct.unpack_from('<I', header, 8)[0]
    entry['page_bytes'] = struct.unpack_from('<I', header, 36)[0]
  if header[:2] == b'MZ':
    pe = struct.unpack_from('<I', header, 0x3c)[0]
    with path.open('rb') as stream:
      stream.seek(pe)
      coff = stream.read(24)
    if coff[:4] == b'PE\0\0':
      entry['format'] = 'PE/COFF'
      entry['machine'] = hex(struct.unpack_from('<H', coff, 4)[0])
  return entry

nabu = project / 'upstream/nabu'
efi = nabu / 'base/overlay/opt/nabu/efi-template'
boot_files = [nabu / 'base/overlay/opt/nabu/alohauefi.img', *sorted(efi.rglob('*.efi'))]
boot = [record(path, nabu) for path in boot_files]
refind = (efi / 'EFI/BOOT/bootaa64.efi').read_bytes()
strings = [s.decode('ascii') for s in re.findall(rb'[ -~]{5,}', refind)]
strings += [s.decode('utf-16-le') for s in re.findall(rb'(?:[ -~]\x00){5,}', refind)]
versions = sorted(set(s for s in strings if re.fullmatch(r'\d+\.\d+(?:\.\d+)?', s)))

firmware_dirs = [root / path for path in (
  'usr/lib/firmware/qcom/sm8150/xiaomi/nabu', 'usr/lib/firmware/novatek',
  'usr/lib/firmware/cirrus', 'usr/lib/firmware/ath10k/WCN3990',
  'usr/lib/firmware/qca', 'usr/share/qcom/sm8150/xiaomi/nabu')]
firmware_files = []
for directory in firmware_dirs:
  if directory.is_dir() and not directory.is_symlink():
    firmware_files.extend(p for p in sorted(directory.rglob('*')) if p.is_file() or p.is_symlink())
for name in ('a630_sqe.fw', 'a640_gmu.bin'):
  path = root / 'usr/lib/firmware/qcom' / name
  if path.is_file():
    firmware_files.append(path)
firmware = [record(path, root) for path in firmware_files]
packages = subprocess.check_output([
  'chroot', str(root), '/usr/bin/pacman', '-Q',
  'linux-firmware-xiaomi-nabu', 'linux-firmware-atheros', 'linux-firmware-whence',
  'alsa-ucm-conf-xiaomi-nabu', 'systemd', 'systemd-ukify', 'rmtfs', 'tqftpserv', 'qrtr', 'hexagonrpcd'
], text=True)
inventory = {
  'scope': 'Local image/build artifacts only; installed tablet firmware versions have not been measured.',
  'sources': json.loads((project / 'sources.json').read_text()),
  'kernel_source': json.loads((project / 'kernel/sources.json').read_text()),
  'references': json.loads((project / 'profiles/nabu/bootchain-references.json').read_text()),
  'efi_and_boot_images': boot,
  'refind_version_strings': versions,
  'alternate_recovery_assets_not_used_by_this_builder': [record(p, nabu) for p in sorted((nabu / 'DBKP').iterdir()) if p.is_file()],
  'packaged_firmware_and_defaults': firmware,
  'cirrus_directory_present': (root / 'usr/lib/firmware/cirrus').is_dir(),
  'firmware_package_versions': dict(line.split(' ', 1) for line in packages.splitlines()),
  'tablet_boot_stages_not_observed': ['Qualcomm boot ROM', 'Xiaomi XBL/ABL', 'trust-zone/hypervisor firmware', 'device-specific persist/calibration', 'slot and rollback metadata'],
}
(output / 'components.json').write_text(json.dumps(inventory, indent=2) + '\n')
print(f'Inventoried {len(boot)} boot/EFI artifacts and {len(firmware)} firmware/default files.')
print('rEFInd version strings:', ', '.join(versions))
print('Cirrus firmware directory present:', inventory['cirrus_directory_present'])
