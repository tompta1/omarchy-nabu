import struct
import sys
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zlib
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from nabu_gpt import tail_boot,header,update_header,SECTOR,ENTRY,LINUX_DATA
from nabu_gpt_writer import load_plan, prepare_fd


class TailBoot(unittest.TestCase):
    def setUp(self):
        self.entries=bytearray(SECTOR)
        for i in range(31):
            start,end=(6+i*2,7+i*2) if i<30 else (256,3000000)
            name='userdata' if i==30 else 'part'+str(i+1)
            self.entries[i*ENTRY:(i+1)*ENTRY]=struct.pack('<16s16sQQQ72s',
                LINUX_DATA.bytes_le,uuid.UUID(int=i+1).bytes_le,start,end,0,name.encode('utf-16le'))
        self.entries=bytes(self.entries)
        self.headers=[]
        for current,alternate,table in ((1,3000005,2),(3000005,1,3000001)):
            h=bytearray(SECTOR);h[:8]=b'EFI PART'
            struct.pack_into('<II',h,8,0x10000,92)
            struct.pack_into('<QQQQ',h,24,current,alternate,6,3000000)
            h[56:72]=uuid.UUID(int=100).bytes_le
            struct.pack_into('<QII',h,72,table,32,ENTRY)
            self.headers.append(update_header(h,self.entries))
        self.start=(3000001-1048576)//256*256

    def plan(self,entries=None,**kw):
        return tail_boot(*self.headers,entries or self.entries,self.entries,
                         kw.get('start',self.start),4*1024**3,uuid.UUID(int=200))

    def test_only_userdata_end_and_new_slot_change_with_valid_crcs(self):
        result=self.plan();new=result[0][1]
        self.assertEqual([lba for lba,_ in result],[3000001,3000005,2,1])
        self.assertEqual(new[:30*ENTRY+40],self.entries[:30*ENTRY+40])
        self.assertEqual(new[30*ENTRY+48:31*ENTRY],self.entries[30*ENTRY+48:31*ENTRY])
        self.assertEqual(struct.unpack_from('<Q',new,30*ENTRY+40)[0],self.start-1)
        header(result[-1][1],new);header(result[1][1],new)
        self.assertEqual(new[31*ENTRY+56:31*ENTRY+74],'nabu_boot'.encode('utf-16le'))

    def test_corruption_and_wrong_tail_geometry_are_rejected(self):
        broken=bytearray(self.entries);broken[1]^=1
        with self.assertRaisesRegex(ValueError,'CRC'):
            self.plan(bytes(broken))
        for start in (self.start+1,256,self.start+256):
            with self.subTest(start=start),self.assertRaises(ValueError):
                self.plan(start=start)

    def test_read_only_preparation_round_trips_through_the_writer(self):
        entries = bytearray(self.entries)
        for i, (name, start, count) in enumerate((('rawdump', 6, 32768),
                ('cust', 32774, 262144), ('rescue', 294918, 32768))):
            struct.pack_into('<QQ', entries, i*ENTRY+32, start, start+count-1)
            struct.pack_into('<72s', entries, i*ENTRY+56, name.encode('utf-16le'))
        for i in range(3, 31):
            start = 327686+i*2
            struct.pack_into('<QQ', entries, i*ENTRY+32, start, start+1 if i < 30 else 3000000)
        ph, bh = [update_header(value, entries) for value in self.headers]
        prefix = bytes(SECTOR) + ph + entries + bytes(3*SECTOR)
        contents = {0: prefix, 3000005*SECTOR: bh, 3000001*SECTOR: bytes(entries)}
        with tempfile.TemporaryDirectory() as directory:
            capture, proposal = (Path(directory)/name for name in ('capture', 'proposal'))
            with patch('nabu_gpt_writer.os.pread', side_effect=lambda fd, n, at: contents[at]), patch('nabu_gpt_writer.os.pwrite') as write:
                prepare_fd(0, 3000006*SECTOR, capture, proposal)
                write.assert_not_called()
            original, changes, _ = load_plan(capture, proposal)
            self.assertEqual(original[1], ph)
            self.assertEqual(changes[0][0], 3000001)
            layout = json.loads((proposal/'layout.json').read_text())
            self.assertEqual(layout['boot_start_lba'], self.start)
            self.assertEqual(layout['esp_partition_bytes'], 128*1024**2)
