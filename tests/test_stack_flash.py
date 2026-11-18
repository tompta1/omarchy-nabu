"""REL-04: full-stack identity, receipt, stop-on-error and resume guards; no USB."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import nabu_stack as stack

FAKE='''import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with Path(os.environ['STACK_CALLS']).open('a') as f:f.write(json.dumps(args)+'\\n')
if args==['devices']:print(os.environ.get('STACK_SERIAL','MOCK-TABLET')+' fastboot')
elif 'getvar' in args:
 key=args[-1]; values={'product':'nabu','current-slot':os.environ.get('STACK_SLOT','a'),'unlocked':'yes'}
 print(key+': '+values.get(key,hex(128)))
elif 'flash' in args and args[args.index('flash')+1]==os.environ.get('STACK_FAIL'):sys.exit(1)
'''


class StackFlash(unittest.TestCase):
    def test_proposed_layout_is_rejected_before_usb_or_image_access(self):
        proposal={'layout_profile':'tail-boot-v1','layout_status':'proposed','boot_partition':'nabu_boot'}
        with patch('nabu_stack.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError,'offline-only'):
                stack.probe('unused','unused',proposal)
            run.assert_not_called()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.output=Path(self.temp.name)
        self.layout={'fastboot_serial':'MOCK-TABLET','boot_a_partition_bytes':128,
                     'esp_partition_bytes':128,'boot_partition_bytes':128,'root_partition_bytes':128}
        record={'schema':1,'board':'nabu','layout':self.layout,
                'layout_sha256':stack.layout_digest(self.layout),'write_order':list(stack.ORDER),
                'artifacts':{},'receipts':{}}
        for part,name in stack.FILES.items():
            p=self.output/name;p.write_bytes((part.encode()+b'\0'*128)[:128])
            record['artifacts'][part]={'file':name,'bytes':128,'sha256':stack.digest(p)}
        for name in ('os','uboot','grub','esp'):
            p=self.output/(name+'-receipt.json');p.write_text('{}\n')
            record['receipts'][name]=stack.digest(p)
        (self.output/'stack.json').write_text(json.dumps(record))
        self.client=self.output/'fastboot';self.client.write_text('#!'+sys.executable+'\n'+FAKE);self.client.chmod(0o755)
        self.calls=self.output/'calls.jsonl'
        self.environment={'STACK_CALLS':str(self.calls)}

    def run_flash(self,**kwargs):
        with patch.dict(os.environ,self.environment):
            stack.flash(self.output,self.layout,str(self.client),**kwargs)

    def writes(self):
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        return [args[args.index('flash')+1] for args in calls if 'flash' in args]

    def test_only_four_reviewed_partitions_are_written_once(self):
        self.run_flash()
        self.assertEqual(self.writes(),list(stack.ORDER))
        self.run_flash(resume=True)
        self.assertEqual(self.writes(),list(stack.ORDER))

    def test_other_device_or_slot_prevents_every_write(self):
        for key,value in (('STACK_SERIAL','ANOTHER-TABLET'),('STACK_SLOT','b')):
            with self.subTest(key=key),patch.dict(os.environ,{key:value}),self.assertRaises(ValueError):
                self.run_flash()
            self.assertEqual(self.writes(),[])

    def test_changed_kernel_or_loader_fails_before_usb_access(self):
        for part in ('boot_a','rawdump','cust','userdata'):
            path=self.output/stack.FILES[part];original=path.read_bytes();path.write_bytes(b'changed')
            with self.subTest(partition=part),self.assertRaises(ValueError):self.run_flash()
            self.assertFalse(self.calls.exists())
            path.write_bytes(original)

    def test_failed_write_stops_and_requires_readback_before_resume(self):
        with patch.dict(os.environ,{'STACK_FAIL':'rawdump'}),self.assertRaises(subprocess.CalledProcessError):
            self.run_flash()
        self.assertEqual(self.writes(),['boot_a','rawdump'])
        with self.assertRaisesRegex(ValueError,'Unconfirmed write'):
            self.run_flash(resume=True)
        self.assertEqual(self.writes(),['boot_a','rawdump'])


if __name__=='__main__':unittest.main()
