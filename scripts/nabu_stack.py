#!/usr/bin/env python3
"""Prepare and flash a measured Nabu stack; preserve GPT, boot_b and calibration."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from nabu_images import digest, layout_digest, verify as verify_os
from nabu_layout import load_layout, require_existing_flash_layout

ORDER = ('boot_a','rawdump','cust','userdata')
FILES = dict(zip(ORDER,('uboot.img','esp.img','boot.img','rootfs.img')))


def sizes(layout):
    return {'boot_a':layout['boot_a_partition_bytes'],
            'rawdump':layout['esp_partition_bytes'],
            'cust':layout['boot_partition_bytes'],'userdata':layout['root_partition_bytes']}


def prepare(output, os_output, uboot, grub, esp, layout):
    require_existing_flash_layout(layout)
    if not re.fullmatch(r'[A-Za-z0-9_-]+',layout.get('fastboot_serial','')):
        raise ValueError('Supply the previously identified device serial in the private layout')
    # The slot size is a flash-only measurement, not an OS filesystem input.
    os_layout={key:value for key,value in layout.items() if key!='boot_a_partition_bytes'}
    verify_os(os_output,os_layout)
    if not (os_output/'build-state').read_text().startswith('offline-verified;'):
        raise ValueError('OS image did not finish offline verification')
    if not (grub/'receipt.json').is_file() or not (uboot/'receipt.json').is_file():
        raise ValueError('Both bootloader build receipts are required')
    esp_receipt=json.loads((esp.parent/'receipt.json').read_text())
    if (esp_receipt['layout_sha256']!=layout_digest(os_layout)
            or esp_receipt['sha256']!=digest(esp) or esp_receipt['bytes']!=esp.stat().st_size):
        raise ValueError('ESP differs from its verified GRUB/layout receipt')
    output.mkdir(parents=True,exist_ok=False)
    inputs={'boot_a':uboot/'u-boot-xiaomi-nabu.img','rawdump':esp,
            'cust':os_output/'images/boot.img','userdata':os_output/'images/rootfs.img'}
    record={'schema':1,'board':'nabu','layout':layout,'layout_sha256':layout_digest(layout),
            'write_order':list(ORDER),'preserve':['boot_b','persist','persistbak','GPT','vendor firmware'],
            'artifacts':{},'hardware_tested':False}
    for partition,path in inputs.items():
        target=output/FILES[partition]
        os.link(path,target)
        record['artifacts'][partition]={'file':target.name,'bytes':target.stat().st_size,'sha256':digest(target)}
    for name,path in (('os',os_output),('uboot',uboot),('grub',grub)):
        receipt='image-receipt.json' if name=='os' else 'receipt.json'
        (output/(name+'-receipt.json')).write_bytes((path/receipt).read_bytes())
        record.setdefault('receipts',{})[name]=digest(output/(name+'-receipt.json'))
    (output/'esp-receipt.json').write_bytes((esp.parent/'receipt.json').read_bytes())
    record['receipts']['esp']=digest(output/'esp-receipt.json')
    (output/'stack.json').write_text(json.dumps(record,indent=2)+'\n')
    verify(output,layout)
    print('Full stack bound to the private device layout; nothing flashed.')


def verify(output,layout):
    require_existing_flash_layout(layout)
    record=json.loads((output/'stack.json').read_text())
    if (record.get('schema')!=1 or record.get('board')!='nabu'
            or record.get('layout')!=layout or record.get('layout_sha256')!=layout_digest(layout)
            or record.get('write_order')!=list(ORDER) or set(record.get('artifacts',{}))!=set(ORDER)):
        raise ValueError('Stack does not match the reviewed device/layout/write set')
    limits=sizes(layout)
    for partition,entry in record['artifacts'].items():
        if entry['file']!=FILES[partition]:raise ValueError('Unexpected artifact path')
        path=output/entry['file']
        if path.stat().st_size!=entry['bytes'] or digest(path)!=entry['sha256']:
            raise ValueError('Changed stack artifact: '+partition)
        if entry['bytes']<=0 or entry['bytes']>limits[partition]:
            raise ValueError('Artifact exceeds its measured partition: '+partition)
        if partition in ('rawdump','cust') and entry['bytes']!=limits[partition]:
            raise ValueError('Boot/ESP image must match the whole measured filesystem')
    if set(record.get('receipts',{}))!={'os','uboot','grub','esp'}:
        raise ValueError('Missing stack build receipts')
    for name,expected in record['receipts'].items():
        if digest(output/(name+'-receipt.json'))!=expected:raise ValueError('Changed build receipt')
    return record


def probe(client,serial,layout):
    require_existing_flash_layout(layout)
    def getvar(key):
        result=subprocess.run([client,'-s',serial,'getvar',key],capture_output=True,text=True,timeout=20,check=True)
        found=re.findall(re.escape(key)+r':\s*(\S+)',result.stdout+result.stderr)
        if len(found)!=1:raise ValueError('Missing/ambiguous FASTBOOT '+key)
        return found[0]
    result=subprocess.check_output([client,'devices'],text=True,timeout=15)
    devices=[line.split()[0] for line in result.splitlines() if len(line.split())==2 and line.split()[1]=='fastboot']
    if not devices:raise ValueError('No FASTBOOT device detected')
    if devices!=[serial] or serial!=layout.get('fastboot_serial'):
        raise ValueError('FASTBOOT identity differs from the recorded device')
    if getvar('product')!='nabu' or getvar('current-slot') not in ('a','_a'):
        raise ValueError('Expected nabu with active boot_a; no slot switching is permitted')
    if getvar('unlocked').lower() not in ('yes','true','1'):
        raise ValueError('Bootloader is not reported unlocked')
    for partition,expected in sizes(layout).items():
        if int(getvar('partition-size:'+partition),16)!=expected:
            raise ValueError('Measured geometry differs: '+partition)
    print('Recorded FASTBOOT device, active slot, unlock state and partition sizes match.',flush=True)


def flash(output,layout,client,reboot=False,resume=False):
    record=verify(output,layout)
    serial=layout.get('fastboot_serial','')
    probe(client,serial,layout)
    journal_path=output/'flash-journal.json'
    identity=digest(output/'stack.json')
    state={'stack_sha256':identity,'writes':{},'reboot_confirmed':False}
    if journal_path.exists():
        state=json.loads(journal_path.read_text())
        if not resume or state.get('stack_sha256')!=identity:
            raise ValueError('Existing flash journal: review it and explicitly resume the same bundle')
        if (any(item['status']!='acknowledged' for item in state['writes'].values())
                or state.get('root_erase',{}).get('status','acknowledged')!='acknowledged'):
            raise ValueError('Unconfirmed write: inspect/read back the device before any further flashing')
    def save():
        temporary=journal_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state,indent=2)+'\n')
        temporary.replace(journal_path)
    with (output/'.flash.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for partition in ORDER:
            if partition in state['writes']:continue
            if partition=='userdata' and 'root_erase' not in state:
                # Sparse DONT_CARE chunks need not clear old ext4/Btrfs signatures.
                # This authorized clean installation must not retain those blocks.
                state['root_erase']={'status':'unconfirmed','started':time.time()};save()
                print('Erasing userdata before the fresh Btrfs filesystem.',flush=True)
                with (output/'erase-userdata.log').open('w') as log:
                    subprocess.run([client,'-s',serial,'erase','userdata'],stdout=log,stderr=subprocess.STDOUT,
                                   check=True,timeout=600)
                state['root_erase']['status']='acknowledged';save()
            state['writes'][partition]={'status':'unconfirmed','started':time.time()};save()
            print('Writing '+partition,flush=True)
            with (output/('flash-'+partition+'.log')).open('w') as log:
                subprocess.run([client,'-s',serial,'-S','256M','flash',partition,
                                str((output/FILES[partition]).resolve())],stdout=log,stderr=subprocess.STDOUT,
                               check=True,timeout=2400 if partition=='userdata' else 600)
            state['writes'][partition]['status']='acknowledged';save()
            print(partition+' write acknowledged.',flush=True)
        if reboot:
            print('All writes acknowledged. Allowing up to ten minutes for reboot/writeback.',flush=True)
            subprocess.run([client,'-s',serial,'reboot'],check=True,timeout=600)
            state['reboot_confirmed']=True;save()
    print('All four partition writes acknowledged. Verify the installed stack after boot.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','verify','probe','flash'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--os',type=Path)
    parser.add_argument('--uboot',type=Path)
    parser.add_argument('--grub',type=Path)
    parser.add_argument('--esp',type=Path)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--reboot',action='store_true')
    args=parser.parse_args()
    try:
        layout=load_layout()
        client=os.environ.get('NABU_FASTBOOT_BIN','fastboot')
        if args.action=='prepare':prepare(args.output,args.os,args.uboot,args.grub,args.esp,layout)
        elif args.action=='verify':verify(args.output,layout)
        elif args.action=='probe':probe(client,layout.get('fastboot_serial',''),layout)
        else:flash(args.output,layout,client,args.reboot,args.resume)
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
        message='Stack operation stopped: '+str(error)
        if args.action=='flash':
            message+='\nAn unconfirmed write/reboot does not prove a safe reset. Leave USB connected; do not repeat writes blindly.'
        raise SystemExit(message)
