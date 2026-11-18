import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from nabu_gpt_writer import apply_fd,verify_original


class SectorWriter(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        image=self.root/'fixture';image.write_bytes(bytes(4096*16))
        self.fd=os.open(image,os.O_RDWR);self.addCleanup(os.close,self.fd)
        self.old={0:bytes(4096),1:bytes(4096),2:bytes(4096),14:bytes(4096),15:bytes(4096)}
        self.changes=[(14,b'a'*4096),(15,b'b'*4096),(2,b'c'*4096),(1,b'd'*4096)]
        self.journal=self.root/'journal.json'

    def test_only_planned_sectors_are_written_and_read_back(self):
        apply_fd(self.fd,self.old,self.changes,self.journal)
        state=json.loads(self.journal.read_text())
        self.assertEqual(state['status'],'verified')
        self.assertEqual(state['completed_lbas'],[14,15,2,1])
        for lba in range(16):
            self.assertEqual(os.pread(self.fd,4096,lba*4096),dict(self.changes).get(lba,bytes(4096)))

    def test_wrong_original_or_existing_journal_prevents_all_writes(self):
        for corrupt in (True,False):
            if corrupt:os.pwrite(self.fd,b'x',0)
            else:
                os.pwrite(self.fd,b'\0',0);self.journal.write_text('{}')
            with patch('nabu_gpt_writer.os.pwrite') as write:
                with self.assertRaises((ValueError,FileExistsError)):
                    apply_fd(self.fd,self.old,self.changes,self.journal)
                write.assert_not_called()

    def test_interruption_after_backup_never_reaches_primary(self):
        actual=os.pwrite
        def interrupted(fd,data,offset):
            if offset==2*4096:raise OSError('simulated disconnect')
            return actual(fd,data,offset)
        with patch('nabu_gpt_writer.os.pwrite',side_effect=interrupted):
            with self.assertRaises(OSError):
                apply_fd(self.fd,self.old,self.changes,self.journal)
        state=json.loads(self.journal.read_text())
        self.assertEqual(state['completed_lbas'],[14,15])
        self.assertEqual(state['pending_lba'],2)
        self.assertEqual(os.pread(self.fd,8192,4096),bytes(8192))
        with self.assertRaises(ValueError):verify_original(self.fd,self.old)
