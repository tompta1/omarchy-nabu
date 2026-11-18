#!/usr/bin/env python3
"""Create a separate Nabu GRUB ESP image, without USB or mounted filesystems."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from nabu_layout import load_layout
from nabu_filesystems import policy, fat_geometry
from nabu_images import digest,layout_digest


def assemble(grub,output,layout):
    receipt=json.loads((grub/'receipt.json').read_text())
    pin=json.loads((Path(__file__).resolve().parents[1]/'profiles/nabu/grub.json').read_text())
    if (receipt['version']!=pin['version'] or not receipt['signature_verified']
            or receipt['signer_fingerprint']!=pin['signer_fingerprint']
            or receipt['package_sha256']!=pin['package_sha256']):
        raise ValueError('GRUB build differs from the signed pinned package')
    for name,key in (('grubaa64-candidate.efi','candidate_sha256'),('grubaa64-rescue.efi','rescue_sha256'),
                     ('grub.cfg','config_sha256'),('rescue.cfg','rescue_config_sha256')):
        if digest(grub/name)!=receipt[key]:raise ValueError('Changed GRUB build: '+name)
    # Both standalone loaders must target the boot identity of this exact set.
    for name in ('grub.cfg','rescue.cfg'):
        if layout['boot_filesystem_uuid'] not in (grub/name).read_text():
            raise ValueError('GRUB is bound to a different boot filesystem')
    output.mkdir(parents=True,exist_ok=False)
    image=output/'esp.img'
    with image.open('xb') as stream:stream.truncate(layout['esp_partition_bytes'])
    settings=policy()
    subprocess.run(['mkfs.fat','-F',str(settings['esp_fat_bits']),'-S',str(settings['esp_sector_size']),
                    '-s','1','-i',layout['esp_filesystem_uuid'].replace('-',''),'-n','OMARCHY_EFI',str(image)],check=True)
    with image.open('rb') as stream:
        geometry = fat_geometry(stream.read(4096))
    if geometry['fat_bits'] != settings['esp_fat_bits']:
        raise ValueError('ESP FAT geometry differs from policy')
    with tempfile.TemporaryDirectory(dir=output,prefix='.esp-files-') as folder:
        tree=Path(folder)
        files={}
        for relative in ('EFI/BOOT/BOOTAA64.EFI','EFI/fedora/grubaa64.efi','EFI/omarchy-nabu/grubaa64.efi'):
            path=tree/relative;path.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(grub/'grubaa64-candidate.efi',path);files[relative]=digest(path)
        path=tree/'EFI/omarchy-nabu/grub-rescue.efi'
        shutil.copyfile(grub/'grubaa64-rescue.efi',path);files[str(path.relative_to(tree))]=digest(path)
        path=tree/'EFI/fedora/grub.cfg'
        path.write_bytes((grub/'grub.cfg').read_bytes());files[str(path.relative_to(tree))]=digest(path)
        subprocess.run(['mcopy','-i',str(image),'-s',str(tree/'EFI'),'::/'],check=True)
        subprocess.run(['fsck.fat','-n',str(image)],check=True)
        for index,(relative,expected) in enumerate(files.items()):
            check=tree/('readback-'+str(index))
            subprocess.run(['mcopy','-i',str(image),'::/'+relative,str(check)],check=True)
            if digest(check)!=expected:raise ValueError('ESP file verification failed: '+relative)
    actual=subprocess.check_output(['blkid','-p','-o','value','-s','UUID',str(image)],text=True).strip()
    if actual.upper()!=layout['esp_filesystem_uuid'].upper():raise ValueError('ESP filesystem identity mismatch')
    record={'schema':1,'layout_sha256':layout_digest(layout),'grub_version':pin['version'],
            'files':files,'sha256':digest(image),'bytes':image.stat().st_size,'hardware_tested':False}
    (output/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
    print('Clean GRUB ESP and independent stock rescue loader verified. Nothing flashed.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--grub',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    assemble(args.grub,args.output,load_layout())
