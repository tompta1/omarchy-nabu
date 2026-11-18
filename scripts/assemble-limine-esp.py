#!/usr/bin/env python3
"""Build a FAT ESP with the tested single Limine/UKI path; never flash it."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
from nabu_filesystems import policy, fat_geometry
from nabu_images import digest, layout_digest
from nabu_layout import load_layout
from nabu_limine import assemble_default, assemble_loader
from nabu_image_boot import boot_checks
from nabu_paths import PROJECT


def assemble(loader, uki, output, layout, filesystem=None, firmware_vars=None):
    output = output.resolve()
    if not output.is_relative_to((PROJECT/'out').resolve()) or output == PROJECT/'out':
        raise ValueError('ESP output must be below ignored out/')
    if layout.get('layout_profile') == 'tail-boot-v1' and firmware_vars is None:
        raise ValueError('Supply the preserved device U-Boot EFI variable file for migration')
    if firmware_vars is not None:
        firmware_vars = Path(firmware_vars)
        if firmware_vars.is_symlink() or not firmware_vars.is_file() or not firmware_vars.stat().st_size:
            raise ValueError('Firmware variable input must be a captured nonempty regular file')
    settings = policy()
    release = boot_checks.uki.inspect(uki, filesystem or settings['root_type']) if uki else None
    output.mkdir(parents=True, exist_ok=False)
    image = output/'esp.img'
    with image.open('xb') as stream:
        stream.truncate(layout['esp_partition_bytes'])
    subprocess.run(['mkfs.fat', '-F', str(settings['esp_fat_bits']), '-S',
                    str(settings['esp_sector_size']), '-s', '1', '-i',
                    layout['esp_filesystem_uuid'].replace('-', ''),
                    '-n', 'OMARCHY_EFI', str(image)], check=True)
    with image.open('rb') as stream:
        geometry = fat_geometry(stream.read(4096))
    if geometry['fat_bits'] != settings['esp_fat_bits']:
        raise ValueError('ESP format differs from policy')
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        tree = Path(temporary)/'tree'
        manifest = assemble_default(loader, uki, tree) if uki else assemble_loader(loader, tree)
        if firmware_vars is not None:
            import shutil
            shutil.copyfile(firmware_vars,tree/'ubootefi.var')
            item={'bytes':firmware_vars.stat().st_size,'sha256':digest(firmware_vars)}
            manifest['files']['ubootefi.var']=item
            manifest['required_bytes']+=item['bytes']
            manifest['preserved_uboot_variables']=True
        if manifest['required_bytes']+8*1024**2 > layout['esp_partition_bytes']:
            raise ValueError('ESP lacks update/staging headroom')
        for name in sorted({Path(name).parts[0] for name in manifest['files']}):
            subprocess.run(['mcopy', '-i', str(image), '-s', str(tree/name), '::/'], check=True)
        for index, (name, item) in enumerate(manifest['files'].items()):
            check = Path(temporary)/str(index)
            subprocess.run(['mcopy', '-i', str(image), '::/'+name, str(check)], check=True)
            if digest(check) != item['sha256']:
                raise ValueError('ESP readback mismatch: '+name)
    subprocess.run(['fsck.fat', '-n', str(image)], check=True)
    actual = subprocess.check_output(['blkid','-p','-o','value','-s','UUID',str(image)], text=True).strip()
    if actual.upper() != layout['esp_filesystem_uuid'].upper():
        raise ValueError('ESP identity mismatch')
    record = {**manifest, 'layout_sha256': layout_digest(layout), 'geometry': geometry,
              'sha256': digest(image), 'bytes': image.stat().st_size,
              'kernel_release': release, 'root_filesystem': filesystem or settings['root_type']}
    (output/'receipt.json').write_text(json.dumps(record, indent=2)+'\n')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--loader', type=Path, required=True)
    content = parser.add_mutually_exclusive_group(required=True)
    content.add_argument('--uki', type=Path, help='Self-contained single-kernel ESP')
    content.add_argument('--loader-only', action='store_true',
                         help='Loader files only; configuration and UKI live on FAT /boot')
    parser.add_argument('--filesystem', choices=('ext4', 'btrfs'))
    parser.add_argument('--firmware-vars', type=Path, help='Private capture of the device ESP ubootefi.var')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        record = assemble(args.loader, args.uki, args.output, load_layout(), args.filesystem, args.firmware_vars)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit('Limine ESP preparation refused: '+str(error))
    print('Limine ESP readback verified; no device accessed.')
