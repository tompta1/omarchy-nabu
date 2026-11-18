"""Construct metadata-only Nabu tail-boot GPT changes; never write a device."""
import struct
import uuid
import zlib

SECTOR = 4096
ENTRY = 128
LINUX_DATA = uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4')


def header(data, entries):
    if len(data) != SECTOR or data[:8] != b'EFI PART':
        raise ValueError('Expected a 4 KiB GPT header')
    revision, size, checksum = struct.unpack_from('<III', data, 8)
    if revision != 0x10000 or not 92 <= size <= SECTOR:
        raise ValueError('Unsupported GPT revision/header length')
    check = bytearray(data[:size]); check[16:20] = bytes(4)
    if zlib.crc32(check) != checksum:
        raise ValueError('GPT header CRC mismatch')
    count, entry_size, crc = struct.unpack_from('<III', data, 80)
    if (count, entry_size) != (32, ENTRY) or len(entries) != count*ENTRY:
        raise ValueError('Expected the measured 32-entry Nabu table')
    if zlib.crc32(entries) != crc:
        raise ValueError('GPT entry CRC mismatch')
    return struct.unpack_from('<QQQQ', data, 24)


def label(entry):
    text = entry[56:128].decode('utf-16le')
    value, _, padding = text.partition('\0')
    if padding.strip('\0'):
        raise ValueError('Malformed GPT partition label')
    return value


def update_header(data, entries):
    result = bytearray(data)
    struct.pack_into('<I', result, 88, zlib.crc32(entries))
    result[16:20] = bytes(4)
    size = struct.unpack_from('<I', result, 12)[0]
    struct.pack_into('<I', result, 16, zlib.crc32(result[:size]))
    return bytes(result)


def tail_boot(primary, backup, entries, backup_entries, boot_start, boot_bytes, partition_uuid):
    """Preserve all entries except userdata's end; populate empty entry 32.

    Return four sector payloads in backup-before-primary order. They are NOT a
    Qualcomm gpt_both flash image. A separate device-bound writer is required.
    """
    first = header(primary, entries)
    second = header(backup, backup_entries)
    if (first[:2] != tuple(reversed(second[:2])) or first[2:] != second[2:]
            or primary[56:72] != backup[56:72] or entries != backup_entries):
        raise ValueError('GPT primary/backup identity or geometry differs')
    primary_table = struct.unpack_from('<Q', primary, 72)[0]
    backup_table = struct.unpack_from('<Q', backup, 72)[0]
    if (first[0] != 1 or primary_table != 2 or first[2] <= primary_table
            or not first[3] < backup_table < second[0]):
        raise ValueError('Unexpected Nabu GPT metadata placement')
    if entries[31*ENTRY:] != bytes(ENTRY):
        raise ValueError('GPT slot 32 is occupied')
    active = [entries[i*ENTRY:(i+1)*ENTRY] for i in range(31)]
    if any(entry[:16] == bytes(16) for entry in active) or label(active[-1]) != 'userdata':
        raise ValueError('Expected userdata as occupied slot 31')
    ranges = [struct.unpack_from('<QQ', entry, 32) for entry in active]
    if (any(start < first[2] or end > first[3] or start > end for start,end in ranges)
            or any(a[1] >= b[0] for a,b in zip(ranges,ranges[1:]))):
        raise ValueError('Invalid/overlapping original GPT partitions')
    if (type(boot_start) is not int or type(boot_bytes) is not int
            or boot_bytes != 4*1024**3 or boot_start % 256
            or not ranges[-1][0] < boot_start
            or not 0 <= ranges[-1][1]+1-boot_start-boot_bytes//SECTOR < 256):
        raise ValueError('Tail boot geometry must be aligned 4 GiB within userdata')
    new_uuid = uuid.UUID(str(partition_uuid))
    if new_uuid.int == 0 or any(entry[16:32] == new_uuid.bytes_le for entry in active):
        raise ValueError('New partition UUID must be unique/nonzero')
    changed = bytearray(entries)
    struct.pack_into('<Q',changed,30*ENTRY+40,boot_start-1)
    changed[31*ENTRY:] = struct.pack('<16s16sQQQ72s', LINUX_DATA.bytes_le,
        new_uuid.bytes_le, boot_start, boot_start+boot_bytes//SECTOR-1, 0,
        'nabu_boot'.encode('utf-16le'))
    changed = bytes(changed)
    ph, bh = update_header(primary,changed),update_header(backup,changed)
    header(ph,changed); header(bh,changed)
    return [(backup_table,changed),(second[0],bh),(primary_table,changed),(first[0],ph)]
