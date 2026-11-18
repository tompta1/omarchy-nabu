#!/usr/bin/env python3
"""Flash qualified Limine/Btrfs files only after the bound UMS GPT migration."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from nabu_gpt_writer import load_plan,plan_digest
from nabu_images import verify as verify_os,digest,layout_digest
from nabu_layout import load_layout


def verify_bundle(output,capture,layout,proposal=None):
    if layout.get('layout_profile')!='tail-boot-v1' or layout['boot_partition']!='nabu_boot':
        raise ValueError('This flasher only handles the reviewed tail-boot layout')
    proposal=proposal if proposal is not None else output/'gpt-proposal'
    _,changes,_=load_plan(capture,proposal)
    journal=json.loads((proposal/'write-journal.json').read_text())
    if (journal.get('status')!='verified' or journal.get('plan_sha256')!=plan_digest(changes)
            or journal.get('completed_lbas')!=[lba for lba,_ in changes] or journal.get('pending_lba') is not None):
        raise ValueError('No matching verified GPT migration journal')
    import struct
    entries=changes[0][1]
    start=struct.unpack_from('<Q',entries,30*128+32)[0]
    boot_start=struct.unpack_from('<Q',entries,31*128+32)[0]
    if (layout['root_partition_bytes']!=(boot_start-start)*4096
            or layout['boot_start_lba']!=boot_start or layout['root_start_lba']!=start):
        raise ValueError('OS layout differs from the migrated GPT')
    verify_os(output,layout)
    esp=output/'esp-component/esp.img'
    receipt=json.loads((esp.parent/'receipt.json').read_text())
    if (receipt['layout_sha256']!=layout_digest(layout) or receipt['sha256']!=digest(esp)
            or receipt['bytes']!=esp.stat().st_size or esp.stat().st_size!=layout['esp_partition_bytes']
            or receipt.get('configuration_volume')!='boot' or not receipt.get('preserved_uboot_variables')):
        raise ValueError('ESP component does not match the reviewed loader/layout')
    return {'nabu_boot':output/'images/boot.img','userdata':output/'images/rootfs.img','rawdump':esp}


def probe(client,serial,layout):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',serial):raise ValueError('Invalid recorded serial')
    devices=subprocess.check_output([client,'devices'],text=True,timeout=20)
    found=[line.split()[0] for line in devices.splitlines() if len(line.split())==2 and line.split()[1]=='fastboot']
    if found!=[serial]:raise ValueError('Exactly the recorded FASTBOOT tablet is required')
    def getvar(key):
        result=subprocess.run([client,'-s',serial,'getvar',key],capture_output=True,text=True,check=True,timeout=25)
        values=re.findall(re.escape(key)+r':\s*(\S+)',result.stdout+result.stderr)
        if len(values)!=1:raise ValueError('Missing/ambiguous '+key)
        return values[0]
    if getvar('product')!='nabu' or getvar('unlocked').lower() not in ('yes','true','1'):
        raise ValueError('Expected the unlocked Nabu')
    if getvar('current-slot') not in ('a','_a'):raise ValueError('Boot slot changed; no slot switching is authorized')
    sizes={'nabu_boot':layout['boot_partition_bytes'],'userdata':layout['root_partition_bytes'],
           'rawdump':layout['esp_partition_bytes'],'cust':1073741824,'rescue':134217728}
    for name,size in sizes.items():
        if int(getvar('partition-size:'+name),16)!=size:raise ValueError('Partition geometry differs: '+name)


def flash(output,files,client,serial,reboot=False,resume=False,proposal=None):
    proposal=proposal if proposal is not None else output/'gpt-proposal'
    identity=hashlib.sha256((digest(output/'image-receipt.json')+
        digest(output/'esp-component/receipt.json')+digest(proposal/'write-journal.json')+serial).encode()).hexdigest()
    path=output/'limine-flash-journal.json'
    state={'bundle_sha256':identity,'completed':[],'pending':None}
    def save():
        temporary=path.with_suffix('.tmp')
        with temporary.open('w') as stream:
            json.dump(state,stream,indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory=os.open(output,os.O_RDONLY|os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    with (output/'.limine-flash.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if path.exists():
            state=json.loads(path.read_text())
            if (not resume or state.get('bundle_sha256')!=identity or state.get('pending') is not None
                    or state.get('completed')!=['erase-userdata','nabu_boot','userdata','rawdump','reboot'][:len(state.get('completed',[]))]):
                raise ValueError('Existing/unconfirmed flash journal: inspect before any retry')
        operations=[('erase-userdata',['erase','userdata'],600)]
        operations += [(name,['-S','256M','flash',name,str(file.resolve())],2400 if name=='userdata' else 600)
                       for name,file in files.items()]
        if reboot:operations.append(('reboot',['reboot'],600))
        for name,args,budget in operations:
            if name in state['completed']:continue
            state['pending']=name;save()
            print('Starting '+name,flush=True)
            with (output/('limine-'+name+'.log')).open('w') as log:
                try:
                    subprocess.run([client,'-s',serial,*args],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=budget)
                finally:
                    os.fsync(log.fileno())
            state['completed'].append(name);state['pending']=None;save()
            print(name+' acknowledged',flush=True)


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--capture',type=Path,required=True)
    parser.add_argument('--proposal',type=Path,help='Local GPT proposal directory with its verified write journal (default: OUTPUT/gpt-proposal)')
    parser.add_argument('--serial',required=True)
    parser.add_argument('--flash',action='store_true')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--reboot',action='store_true')
    args=parser.parse_args()
    layout=load_layout()
    files=verify_bundle(args.output,args.capture,layout,args.proposal)
    client=os.environ.get('NABU_FASTBOOT_BIN','fastboot')
    probe(client,args.serial,layout)
    print('Recorded tablet, migrated geometry and verified images match.',flush=True)
    if args.flash:flash(args.output,files,client,args.serial,args.reboot,args.resume,proposal=args.proposal)


if __name__=='__main__':
    try:main()
    except subprocess.TimeoutExpired:
        raise SystemExit('Fastboot timed out. Inspect the operation log and journal before retrying.')
    except subprocess.CalledProcessError as error:
        raise SystemExit(f'Fastboot stopped with status {error.returncode}. Inspect the operation log and journal before retrying.')
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        raise SystemExit('Flash stopped: '+str(error)+'\nDo not repeat unconfirmed operations or force Power while writeback may be active.')
