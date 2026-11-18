#!/usr/bin/env python3
"""Create a private image variant retaining this tablet's Wi-Fi/BT connections."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
from nabu_images import create, verify, digest
from nabu_layout import load_layout
from nabu_paths import PROJECT, project_relative, kernel_input


def quote(value):
    if any(ord(c)<32 for c in str(value)):
        raise ValueError('Control character in backup path')
    return '"'+str(value).replace('\\','\\\\').replace('"','\\"')+'"'


def selected(name):
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Unsafe backup path')
    value=str(path)
    return (value.startswith('etc/NetworkManager/system-connections/') and value.endswith('.nmconnection')
            or value.startswith('var/lib/bluetooth/'))


if __name__=='__main__':
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--backup',required=True)
    args=parser.parse_args()
    source=PROJECT/project_relative(args.source)
    output=PROJECT/project_relative(args.output)
    backup=PROJECT/project_relative(args.backup)
    if not output.is_relative_to(PROJECT/'out') or output.exists():
        raise SystemExit('Choose an unused output under out/')
    layout=load_layout()
    original=verify(source,layout)
    assert original['kernel']['release']==(kernel_input()/'kernelrelease').read_text().strip()
    output.mkdir(parents=True,mode=0o700)
    (output/'images').mkdir(mode=0o700)
    subprocess.run(['cp','--reflink=auto','--sparse=always',str(source/'images/rootfs.img'),str(output/'images/rootfs.img')],check=True)
    subprocess.run(['cp','--reflink=auto',str(source/'images/boot.img'),str(output/'images/boot.img')],check=True)
    for name in ('sources.json','packages.lock','bootstrap.sha256','keyboard-source.json'):
        shutil.copy2(source/name,output/name)
    if (source/'image-options.json').is_file():
        shutil.copy2(source/'image-options.json',output/'image-options.json')
    imported=0
    with tempfile.TemporaryDirectory(prefix='nabu-connections-') as directory,tarfile.open(backup) as archive:
        temporary=Path(directory)
        commands=[]
        files=[]
        for member in archive:
            if not member.isfile() or not selected(member.name):continue
            if member.size>2*1024*1024:raise ValueError('Oversized connection-state file')
            target='/'+str(PurePosixPath(member.name))
            data=archive.extractfile(member).read()
            local=temporary/str(imported)
            local.write_bytes(data)
            parents=list(PurePosixPath(target).parents)[:-1]
            for parent in reversed(parents):commands.append('mkdir '+quote(parent))
            commands.extend(['rm '+quote(target),'write '+quote(local)+' '+quote(target),
                             'set_inode_field '+quote(target)+' mode 0100600',
                             'set_inode_field '+quote(target)+' uid 0',
                             'set_inode_field '+quote(target)+' gid 0'])
            files.append((target,hashlib.sha256(data).hexdigest()))
            imported+=1
        if not imported:raise ValueError('Backup has no selected connection state')
        command_file=temporary/'commands'
        command_file.write_text('\n'.join(commands)+'\n')
        with (output/'private-provision.log').open('w') as log:
            subprocess.run(['debugfs','-w','-f',str(command_file),str(output/'images/rootfs.img')],check=True,stdout=log,stderr=log)
        for index,(target,expected) in enumerate(files):
            dumped=temporary/('verify-'+str(index))
            subprocess.run(['debugfs','-R','dump '+quote(target)+' '+quote(dumped),str(output/'images/rootfs.img')],check=True,capture_output=True)
            if digest(dumped)!=expected:raise ValueError('Connection-state verification failed')
    subprocess.run(['e2fsck','-fn',str(output/'images/rootfs.img')],check=True)
    receipt={'private_variant':True,'imported_files':imported,
             'base_receipt_sha256':digest(source/'image-receipt.json'),
             'backup_sha256':digest(backup),'scope':['Wi-Fi connections','Bluetooth pairing']}
    (output/'private-state-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    create(output,layout)
    with (output/'SHA256SUMS').open('w') as stream:
        for name in ('images/rootfs.img','images/boot.img','image-receipt.json','private-state-receipt.json'):
            stream.write(digest(output/name)+'  '+name+'\n')
    print(f'Private variant verified with {imported} connection-state files; host keys remain first-boot generated.')
