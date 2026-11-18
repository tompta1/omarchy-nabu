"""Nabu Btrfs root policy and mount-free filesystem image construction (OS-11)."""
import argparse
import json
from pathlib import Path
import os
import shutil
import subprocess
import uuid

PROJECT = Path(__file__).resolve().parents[1]


def policy():
    return json.loads((PROJECT/'profiles/nabu/filesystems.json').read_text())


def fstab(layout):
    settings = policy()
    entries = []
    for name, mount in settings['subvolumes'].items():
        options = settings['mount_options']+['subvol='+name]
        if mount == '/':
            options += ['x-systemd.growfs']
        entries.append(f'UUID={layout["root_filesystem_uuid"]} {mount} btrfs '+','.join(options)+' 0 0\n')
    boot_type = layout.get('boot_filesystem_type', settings['boot_type'])
    if boot_type not in ('ext4', 'vfat'):
        raise ValueError('Unsupported boot filesystem')
    boot_options = 'umask=0077,shortname=winnt' if boot_type == 'vfat' else 'defaults'
    esp_mount = '/efi' if boot_type == 'vfat' else '/boot/efi'
    entries += [f'UUID={layout["boot_filesystem_uuid"]} /boot {boot_type} {boot_options} 0 2\n',
                f'UUID={layout["esp_filesystem_uuid"]} {esp_mount} vfat umask=0077,shortname=winnt 0 2\n',
                'PARTLABEL=persist /mnt/vendor/persist ext4 ro,noatime,nofail 0 2\n']
    return ''.join(entries)


def build(stage, image, filesystem_uuid, limit):
    """Consume an isolated staging tree containing @; never mount an image."""
    stage, image = Path(stage), Path(image)
    uuid.UUID(filesystem_uuid)
    if not (stage/'@').is_dir() or image.exists():
        raise ValueError('Use a staged @ tree and a new image path')
    settings = policy()
    for name, mount in settings['subvolumes'].items():
        if mount == '/':
            continue
        source = stage/'@'/mount.lstrip('/')
        source.mkdir(parents=True, exist_ok=True)
        original = source.stat()
        source.rename(stage/name)
        source.mkdir(mode=original.st_mode & 0o7777)
        shutil.copystat(stage/name, source)
        if os.geteuid() == 0:
            os.chown(source, original.st_uid, original.st_gid)
    size_kib = int(subprocess.check_output(['du','-sk',str(stage)], text=True).split()[0])
    size = max(512*1024**2, ((size_kib//1024)+4096)*1024**2)
    if size > limit:
        raise ValueError('Root image exceeds the measured userdata partition')
    with image.open('xb') as stream:
        stream.truncate(size)
    command = ['mkfs.btrfs','-f','-K','-L','OMARCHY','-U',filesystem_uuid,
               '--sectorsize','4096','--rootdir',str(stage)]
    for name in settings['subvolumes']:
        command += ['--subvol',('default:' if name == '@' else 'rw:')+name]
    subprocess.run(command+[str(image)], check=True)
    if image.stat().st_size > limit:
        raise ValueError('Populated Btrfs image exceeds the measured partition')
    subprocess.run(['btrfs','check','--readonly','--check-data-csum',str(image)], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--uuid', required=True)
    parser.add_argument('--limit', type=int, required=True)
    args = parser.parse_args()
    build(args.stage, args.image, args.uuid, args.limit)


def fat_geometry(data):
    """Reject BPBs whose table layout disagrees with the FAT cluster rules."""
    import struct
    if len(data) < 512:
        raise ValueError('Truncated FAT boot sector')
    u16 = lambda n: struct.unpack_from('<H', data, n)[0]
    u32 = lambda n: struct.unpack_from('<I', data, n)[0]
    sector, cluster = u16(11), data[13]
    if sector not in (512, 1024, 2048, 4096) or cluster not in (1, 2, 4, 8, 16, 32, 64, 128):
        raise ValueError('Invalid FAT sector/cluster size')
    total = u16(19) or u32(32)
    fat = u16(22) or u32(36)
    metadata = u16(14)+data[16]*fat+(u16(17)*32+sector-1)//sector
    if not u16(14) or not 1 <= data[16] <= 4 or not fat or metadata >= total:
        raise ValueError('Invalid FAT metadata geometry')
    count = (total-metadata)//cluster
    bits = 12 if count < 4085 else 16 if count < 65525 else 32
    if (bits == 32 and (u16(22) or u16(17) or not u32(36))
            or bits != 32 and (not u16(22) or not u16(17))):
        raise ValueError('FAT table layout disagrees with cluster count')
    return {'fat_bits': bits, 'sector_bytes': sector, 'clusters': count,
            'filesystem_bytes': total*sector}


def build_boot_fat(tree, image, filesystem_uuid, size):
    """Materialize an isolated boot tree as valid FAT32; never mount devices."""
    import re
    import tempfile
    tree, image = Path(tree), Path(image)
    if image.exists() or not tree.is_dir() or type(size) is not int or size <= 0:
        raise ValueError('Use an isolated boot tree and a new image')
    if not re.fullmatch(r'[0-9a-fA-F]{4}-[0-9a-fA-F]{4}', filesystem_uuid):
        raise ValueError('FAT boot UUID must use the FAT serial format')
    seen = set()
    for path in tree.rglob('*'):
        relative = path.relative_to(tree)
        if any(re.search(r'[<>:"\\|?*]', part) or part.endswith((' ', '.'))
               for part in relative.parts):
            raise ValueError('Invalid FAT boot filename')
        folded = str(relative).casefold()
        if folded in seen:
            raise ValueError('Case-colliding FAT boot paths')
        seen.add(folded)
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(tree.resolve()) or not resolved.is_file():
                raise ValueError('Boot symlink leaves the tree or targets a directory')
        elif not path.is_dir() and not path.is_file():
            raise ValueError('Unsupported boot file type')
    with tempfile.TemporaryDirectory(prefix='nabu-fat-boot-') as temporary:
        stage = Path(temporary)/'boot'
        shutil.copytree(tree, stage, symlinks=False)
        required = sum(p.stat().st_size for p in stage.rglob('*') if p.is_file())
        if required+16*1024**2 > size:
            raise ValueError('FAT boot image lacks staging headroom')
        with image.open('xb') as stream:
            stream.truncate(size)
        subprocess.run(['mkfs.fat','-F','32','-S','4096','-s','1','-i',
                        filesystem_uuid.replace('-',''),'-n','OMARCHYBOOT',str(image)],check=True)
        with image.open('rb') as stream:
            geometry = fat_geometry(stream.read(4096))
        if geometry['fat_bits'] != 32:
            raise ValueError('Boot image is too small for valid FAT32 geometry')
        for child in stage.iterdir():
            subprocess.run(['mcopy','-i',str(image),'-s',str(child),'::/'],check=True)
        subprocess.run(['fsck.fat','-n',str(image)],check=True)
        check = Path(temporary)/'readback'
        for source in stage.rglob('*'):
            if source.is_file():
                subprocess.run(['mcopy','-i',str(image),'::/'+str(source.relative_to(stage)),str(check)],check=True)
                if check.read_bytes() != source.read_bytes():
                    raise ValueError('FAT boot file readback mismatch')
                check.unlink()
