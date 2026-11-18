#!/usr/bin/env python3
"""Prepare an opt-in initrd USB root console; no flash and no OS/kernel rebuild."""
import argparse
import json
from pathlib import Path
import shutil
import stat
import tempfile
from nabu_image_boot import Ext4Image,boot_checks

PROJECT=Path(__file__).resolve().parents[1]


def cpio(entries):
    data=bytearray()
    def put(name,mode,content,ino):
        name=name.encode()+b'\0'
        values=(ino,mode,0,0,1,0,len(content),0,0,0,0,len(name),0)
        data.extend(b'070701'+b''.join(f'{value:08x}'.encode() for value in values)+name)
        data.extend(b'\0'*((-len(data))%4));data.extend(content);data.extend(b'\0'*((-len(data))%4))
    directories=set()
    for name in entries:
        directories.update(str(p) for p in Path(name).parents if str(p)!='.')
    ino=1
    for name in sorted(directories,key=lambda n:(n.count('/'),n)):
        put(name,stat.S_IFDIR|0o755,b'',ino);ino+=1
    for name,(mode,content) in entries.items():put(name,mode,content,ino);ino+=1
    put('TRAILER!!!',0,b'',ino)
    return bytes(data)


def prepare(boot,output):
    output.mkdir(parents=True,exist_ok=False)
    source=PROJECT/'profiles/nabu/rescue'
    with tempfile.TemporaryDirectory() as temporary:
        reader=Ext4Image(boot,Path(temporary))
        uki=reader.read_file('/EFI/arch/arch-linux-nabu-local.efi')
        sections=boot_checks.uki.read_sections(uki,filesystem='btrfs')
        for key,name in ((b'.linux','Image'),(b'.initrd','base.initrd'),(b'.dtb','nabu.dtb')):
            (output/name).write_bytes(sections[key])
        (output/'release').write_bytes(sections[b'.uname'].rstrip(b'\0\n')+b'\n')
        args=sections[b'.cmdline'].rstrip(b'\0').decode().split()
        args=['ro' if arg=='rw' else arg for arg in args]
        args+=['systemd.mask=omarchy-nabu-tuning.service']
        (output/'cmdline').write_text(' '.join(args)+'\n')
        (output/'original-grub.cfg').write_bytes(reader.read_file('/grub2/grub.cfg').read_bytes())
    entries={
        'usr/libexec/nabu-usb-rescue':(stat.S_IFREG|0o755,(source/'usb-console.sh').read_bytes()),
        'etc/systemd/system/nabu-usb-rescue.service':(stat.S_IFREG|0o644,(source/'nabu-usb-rescue.service').read_bytes()),
        'etc/systemd/system/initrd.target.wants/nabu-usb-rescue.service':(stat.S_IFLNK|0o777,b'../nabu-usb-rescue.service'),
        'etc/systemd/system/initrd-switch-root.service.d/90-nabu-usb-rescue.conf':(stat.S_IFREG|0o644,(source/'switch-root.conf').read_bytes())}
    (output/'rescue.cpio').write_bytes(cpio(entries))
    (output/'purpose.json').write_text(json.dumps({'physical_usb_root_console':True,'root_read_only':True,
        'default_image_component':False,'kernel_recompiled':False,'physical_boot_tested':False},indent=2)+'\n')
    print('Opt-in USB rescue inputs prepared. Reuse the existing kernel and initrd; nothing flashed.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boot',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    prepare(args.boot,args.output)
