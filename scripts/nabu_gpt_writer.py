#!/usr/bin/env python3
"""Device-bound four-sector GPT writer for U-Boot UMS; probe by default."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import uuid
from nabu_gpt import ENTRY, SECTOR, header, label, tail_boot


def load_plan(capture, proposal):
    capture, proposal = Path(capture), Path(proposal)
    ph, bh, pe, be = [(capture/name).read_bytes() for name in
                      ('primary-header.bin','backup-header.bin','primary-entries.bin','backup-entries.bin')]
    record = json.loads((proposal/'proposal.json').read_text())
    if record['sector_bytes'] != SECTOR or len(record['writes']) != 4:
        raise ValueError('Unsupported GPT write proposal')
    first = record['writes'][0]
    if first['file'] != 'metadata-0.bin':
        raise ValueError('Unexpected proposal payload')
    new_entries = (proposal/first['file']).read_bytes()
    if len(new_entries) != SECTOR:
        raise ValueError('Invalid proposed table size')
    boot_start = struct.unpack_from('<Q',new_entries,31*128+32)[0]
    changes = tail_boot(ph,bh,pe,be,boot_start,4*1024**3,record['new_partition_uuid'])
    disk_bytes = (struct.unpack_from('<Q',ph,32)[0]+1)*SECTOR
    if record['disk_bytes'] != disk_bytes:
        raise ValueError('Proposal disk size differs from capture')
    for index,((lba,data),item) in enumerate(zip(changes,record['writes'])):
        if (item['file'] != f'metadata-{index}.bin' or item['lba'] != lba
                or item['bytes'] != SECTOR or item['sha256'] != hashlib.sha256(data).hexdigest()
                or (proposal/item['file']).read_bytes() != data):
            raise ValueError('Proposal differs from the constrained GPT transformation')
    prefix = (capture/'original-prefix.bin').read_bytes()
    if len(prefix) != 6*SECTOR or prefix[SECTOR:2*SECTOR] != ph or prefix[2*SECTOR:3*SECTOR] != pe:
        raise ValueError('Original GPT prefix differs from captured headers/table')
    old = {0:prefix[:SECTOR],1:ph,2:pe,
           struct.unpack_from('<Q',bh,24)[0]:bh,struct.unpack_from('<Q',bh,72)[0]:be}
    return old,changes,disk_bytes


def verify_original(fd, original):
    for lba,data in original.items():
        if os.pread(fd,len(data),lba*SECTOR) != data:
            raise ValueError(f'Device no longer matches captured GPT sector {lba}; no automatic retry')


def plan_digest(changes):
    value=hashlib.sha256()
    for lba,data in changes:
        value.update(struct.pack('<Q',lba));value.update(data)
    return value.hexdigest()


def prepare_fd(fd, disk_bytes, capture, proposal):
    prefix = os.pread(fd, 6 * SECTOR, 0)
    if len(prefix) != 6 * SECTOR:
        raise ValueError('Incomplete original GPT prefix')
    ph, pe = prefix[SECTOR:2*SECTOR], prefix[2*SECTOR:3*SECTOR]
    current, backup, _, last_usable = header(ph, pe)
    if current != 1 or (backup + 1) * SECTOR != disk_bytes:
        raise ValueError('GPT header does not match the UMS disk geometry')
    bh = os.pread(fd, SECTOR, backup * SECTOR)
    if len(bh) != SECTOR or bh[:8] != b'EFI PART':
        raise ValueError('Missing backup GPT header')
    backup_table = struct.unpack_from('<Q', bh, 72)[0]
    if not last_usable < backup_table < backup:
        raise ValueError('Backup GPT table is outside the metadata area')
    be = os.pread(fd, SECTOR, backup_table * SECTOR)
    start, end = struct.unpack_from('<QQ', pe, 30 * ENTRY + 32)
    boot_bytes = 4 * 1024**3
    boot_start = (end + 1 - boot_bytes // SECTOR) // 256 * 256
    partition_uuid = uuid.uuid4()
    changes = tail_boot(ph, bh, pe, be, boot_start, boot_bytes, partition_uuid)
    partitions = {}
    for index in range(31):
        entry = pe[index*ENTRY:(index+1)*ENTRY]
        first, last = struct.unpack_from('<QQ', entry, 32)
        name = label(entry)
        if not name or name in partitions or name == 'nabu_boot':
            raise ValueError('Original GPT has missing or conflicting partition labels')
        partitions[name] = (last - first + 1) * SECTOR
    sizes = {'rawdump': 128 * 1024**2, 'cust': 1024**3, 'rescue': 128 * 1024**2}
    if any(partitions.get(name) != size for name, size in sizes.items()):
        raise ValueError('Original GPT does not match the supported Nabu partition sizes')

    def fat_uuid():
        value = uuid.uuid4().hex[:8].upper()
        return value[:4] + '-' + value[4:]

    layout = {'layout_profile': 'tail-boot-v1', 'layout_status': 'proposed',
              'root_partition': 'userdata', 'root_partition_bytes': (boot_start-start)*SECTOR,
              'root_start_lba': start, 'boot_start_lba': boot_start,
              'original_root_partition_bytes': (end-start+1)*SECTOR,
              'root_filesystem_uuid': str(uuid.uuid4()),
              'boot_partition': 'nabu_boot', 'boot_partition_bytes': boot_bytes,
              'boot_filesystem_type': 'vfat', 'boot_filesystem_uuid': fat_uuid(),
              'esp_partition': 'rawdump', 'esp_partition_bytes': partitions['rawdump'],
              'esp_filesystem_uuid': fat_uuid()}
    files = {'original-prefix.bin': prefix, 'primary-header.bin': ph, 'primary-entries.bin': pe,
             'backup-header.bin': bh, 'backup-entries.bin': be}
    writes = [{'file': f'metadata-{i}.bin', 'lba': lba, 'bytes': SECTOR,
               'sha256': hashlib.sha256(data).hexdigest()}
              for i, (lba, data) in enumerate(changes)]
    record = {'disk_bytes': disk_bytes, 'sector_bytes': SECTOR,
              'new_partition_uuid': str(partition_uuid), 'writes': writes}
    for directory in (capture, proposal):
        directory.mkdir(mode=0o700)
        owner = directory.parent.stat()
        if os.geteuid() == 0:
            os.chown(directory, owner.st_uid, owner.st_gid)
    for directory, items in ((capture, files), (proposal, {
            **{item['file']: data for item, (_, data) in zip(writes, changes)},
            'proposal.json': (json.dumps(record, indent=2)+'\n').encode(),
            'layout.json': (json.dumps(layout, indent=2)+'\n').encode()})):
        owner = directory.stat()
        for name, data in items.items():
            with (directory/name).open('xb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                if os.geteuid() == 0:
                    os.fchown(stream.fileno(), owner.st_uid, owner.st_gid)
                stream.write(data)


def apply_fd(fd, original, changes, journal):
    """Shared fixture/device implementation; caller verifies USB/block geometry."""
    verify_original(fd,original)
    journal=Path(journal)
    state={'status':'started','completed_lbas':[],'pending_lba':None,
           'plan_sha256':plan_digest(changes)}
    # Exclusive creation prevents blindly repeating an interrupted transaction.
    with journal.open('x') as stream:
        json.dump(state,stream);stream.flush();os.fsync(stream.fileno())
    journal.chmod(0o600)
    if os.geteuid()==0:
        owner=journal.parent.stat();os.chown(journal,owner.st_uid,owner.st_gid)
    def save():
        temporary=journal.with_suffix(journal.suffix+'.tmp')
        with temporary.open('w') as stream:
            json.dump(state,stream);stream.flush();os.fsync(stream.fileno())
        temporary.chmod(0o600)
        if os.geteuid()==0:
            owner=journal.parent.stat();os.chown(temporary,owner.st_uid,owner.st_gid)
        os.replace(temporary,journal)
    try:
        for lba,data in changes:
            state['pending_lba']=lba;save()
            if os.pwrite(fd,data,lba*SECTOR) != len(data):
                raise OSError('Short GPT write; inspect before any retry')
            os.fsync(fd)
            if os.pread(fd,len(data),lba*SECTOR) != data:
                raise OSError('GPT readback mismatch; inspect before any retry')
            state['completed_lbas'].append(lba)
            state['pending_lba']=None;save()
        state['status']='verified';save()
    except BaseException:
        state['status']='interrupted; inspect device and journal before any further write'
        save()
        raise


def usb_block_guard(path,fd,disk_bytes):
    info=os.fstat(fd)
    if not stat.S_ISBLK(info.st_mode):
        raise ValueError('Device must be a whole USB block disk')
    dev=f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'
    location=(Path('/sys/dev/block')/dev).resolve(strict=True)
    if (location/'partition').exists() or not any(re.fullmatch(r'usb[0-9]+',p.name) for p in location.parents):
        raise ValueError('Expected a whole USB UMS disk, not a host disk or partition')
    sector=struct.unpack('I',fcntl.ioctl(fd,0x1268,bytes(4)))[0]  # BLKSSZGET
    size=struct.unpack('Q',fcntl.ioctl(fd,0x80081272,bytes(8)))[0]  # BLKGETSIZE64
    if sector != SECTOR or size != disk_bytes:
        raise ValueError('USB disk geometry differs from the captured tablet')
    tree=json.loads(subprocess.check_output(['lsblk','--json','--output','NAME,MAJ:MIN,MOUNTPOINTS',str(path)],text=True))
    identities=set()
    def visit(nodes):
        for node in nodes:
            identities.add(node['maj:min'])
            if any(node.get('mountpoints') or []):
                raise ValueError('UMS disk or a child partition is mounted')
            holders=Path('/sys/dev/block')/node['maj:min']/'holders'
            if holders.is_dir() and any(holders.iterdir()):
                raise ValueError('UMS disk has active block-device holders')
            visit(node.get('children',[]))
    visit(tree['blockdevices'])
    for row in Path('/proc/swaps').read_text().splitlines()[1:]:
        swap=Path(row.split()[0])
        if swap.exists():
            data=swap.stat()
            if stat.S_ISBLK(data.st_mode) and f'{os.major(data.st_rdev)}:{os.minor(data.st_rdev)}' in identities:
                raise ValueError('UMS disk has active swap')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device',type=Path,required=True)
    parser.add_argument('--capture',type=Path,required=True)
    parser.add_argument('--proposal',type=Path,required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare', action='store_true', help='Read the original GPT and create a local capture and proposal')
    mode.add_argument('--apply',action='store_true',help='Write only the four verified metadata sectors')
    args=parser.parse_args()
    path=args.device.resolve(strict=True)
    if not stat.S_ISBLK(path.stat().st_mode):
        raise ValueError('CLI requires a block device; file fixtures use apply_fd directly')
    flags=(os.O_RDWR if args.apply else os.O_RDONLY)|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW
    fd=os.open(path,flags)
    try:
        if args.prepare:
            project = Path(__file__).resolve().parents[1]
            local = project / 'local'
            capture, proposal = args.capture.resolve(), args.proposal.resolve()
            if any(not item.is_relative_to(local) or item == local or item.exists() or not item.parent.is_dir()
                   for item in (capture, proposal)) or capture == proposal:
                raise ValueError('Use two new output directories inside the project’s ignored local/')
            disk_bytes = struct.unpack('Q', fcntl.ioctl(fd, 0x80081272, bytes(8)))[0]
            usb_block_guard(path, fd, disk_bytes)
            prepare_fd(fd, disk_bytes, capture, proposal)
            print('Original GPT captured and 4 GiB tail-boot proposal prepared. No device writes performed.')
            return
        original,changes,disk_bytes=load_plan(args.capture,args.proposal)
        usb_block_guard(path,fd,disk_bytes)
        verify_original(fd,original)
        if args.apply:
            apply_fd(fd,original,changes,args.proposal/'write-journal.json')
            print('Four GPT metadata sectors written and read back. Do not boot the old root; flash the matching images next.')
        else:
            print('USB disk geometry, unmounted state and original GPT bytes match. No writes performed.')
    finally:
        os.close(fd)


if __name__=='__main__':
    try:
        main()
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        raise SystemExit('GPT operation stopped: '+str(error))
