import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import nabu_limine_flash as flash


class FlashProtocol(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for name in ('image-receipt.json','esp-component/receipt.json','gpt-proposal/write-journal.json'):
            file=self.root/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('{}')
        self.files={}
        for name in ('nabu_boot','userdata','rawdump'):
            file=self.root/(name+'.img');file.write_bytes(b'fixture');self.files[name]=file

    def test_exact_write_set_and_acknowledged_resume(self):
        with patch('nabu_limine_flash.subprocess.run') as run:
            flash.flash(self.root,self.files,'fake','MOCK',reboot=False)
            commands=[call.args[0] for call in run.call_args_list]
            self.assertEqual(commands[0],['fake','-s','MOCK','erase','userdata'])
            self.assertEqual([cmd[cmd.index('flash')+1] for cmd in commands[1:]],['nabu_boot','userdata','rawdump'])
            self.assertFalse(any('boot_a' in cmd or 'cust' in cmd for cmd in commands))
            run.reset_mock()
            with self.assertRaisesRegex(ValueError,'journal'):
                flash.flash(self.root,self.files,'fake','MOCK')
            run.assert_not_called()
            flash.flash(self.root,self.files,'fake','MOCK',resume=True)
            run.assert_not_called()

    def test_unconfirmed_write_stops_and_cannot_be_retried(self):
        def result(cmd,**kw):
            if 'flash' in cmd and cmd[cmd.index('flash')+1]=='userdata':
                state=json.loads((self.root/'limine-flash-journal.json').read_text())
                self.assertEqual(state['completed'],['erase-userdata','nabu_boot'])
                self.assertEqual(state['pending'],'userdata')
                raise subprocess.TimeoutExpired(cmd,2400)
        with patch('nabu_limine_flash.subprocess.run',side_effect=result) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                flash.flash(self.root,self.files,'fake','MOCK')
            state=json.loads((self.root/'limine-flash-journal.json').read_text())
            self.assertEqual(state['completed'],['erase-userdata','nabu_boot'])
            self.assertEqual(state['pending'],'userdata')
            run.reset_mock()
            with self.assertRaisesRegex(ValueError,'journal'):
                flash.flash(self.root,self.files,'fake','MOCK',resume=True)
            run.assert_not_called()

    def test_old_geometry_is_rejected_before_any_write(self):
        layout={'boot_partition_bytes':4*1024**3,'root_partition_bytes':1000,'esp_partition_bytes':128}
        def reply(cmd,**kw):
            key=cmd[-1]
            values={'product':'nabu','unlocked':'yes','current-slot':'a','partition-size:nabu_boot':'0x40000000'}
            return subprocess.CompletedProcess(cmd,0,stdout='',stderr=key+': '+values[key])
        with patch('nabu_limine_flash.subprocess.check_output',return_value='MOCK fastboot\n'), \
             patch('nabu_limine_flash.subprocess.run',side_effect=reply) as run:
            with self.assertRaisesRegex(ValueError,'geometry'):
                flash.probe('fake','MOCK',layout)
            self.assertTrue(all('getvar' in call.args[0] for call in run.call_args_list))

    def test_verified_gpt_journal_is_required_before_large_image_verification(self):
        layout={'layout_profile':'tail-boot-v1','boot_partition':'nabu_boot'}
        with patch('nabu_limine_flash.load_plan',return_value=({},[(1,b'new')],100)), \
             patch('nabu_limine_flash.verify_os') as verify:
            with self.assertRaisesRegex(ValueError,'GPT migration journal'):
                flash.verify_bundle(self.root,self.root,layout)
            verify.assert_not_called()

    def test_resume_is_bound_to_external_proposal_journal(self):
        proposal=self.root/'device-metadata'
        proposal.mkdir()
        journal=proposal/'write-journal.json'
        journal.write_text('{"status":"verified"}')
        with patch('nabu_limine_flash.subprocess.run') as run:
            flash.flash(self.root,self.files,'fake','MOCK',proposal=proposal)
            journal.write_text('{"status":"different"}')
            run.reset_mock()
            with self.assertRaisesRegex(ValueError,'journal'):
                flash.flash(self.root,self.files,'fake','MOCK',resume=True,proposal=proposal)
            run.assert_not_called()
