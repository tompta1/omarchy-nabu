"""Exercise flash guards using a fake fastboot executable; never access USB."""
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import nabu_images

ROOT = Path(__file__).resolve().parents[1]
FAKE = '''import json, os, sys
from pathlib import Path
args=sys.argv[1:]
with Path(os.environ['FAKE_FASTBOOT_LOG']).open('a') as out:
    out.write(json.dumps(args)+'\\n')
if args == ['devices']:
    print('MOCK-DEVICE fastboot')
elif 'getvar' in args:
    key=args[-1]
    if key == 'product': print('product: '+os.environ.get('FAKE_PRODUCT','nabu'))
    else:
        sizes={'userdata':114713931776,'cust':1073741824,'rawdump':134217728}
        value=sizes[key.split(':')[1]]
        if os.environ.get('FAKE_WRONG_SIZE') == key: value-=4096
        print(key+': '+hex(value))
elif 'flash' not in args and args[-1:] != ['reboot']:
    sys.exit(9)
if 'flash' in args and args[args.index('flash')+1] == os.environ.get('FAKE_FAIL_PARTITION'):
    sys.exit(1)
if args[-1:] == ['reboot']:
    sys.exit(int(os.environ.get('FAKE_REBOOT_EXIT','0')))
'''
TIMEOUT = '''import json, os, sys
from pathlib import Path
with Path(os.environ['FAKE_TIMEOUT_LOG']).open('a') as out:
    out.write(json.dumps(sys.argv[1:])+'\\n')
os.execv(os.environ['REAL_TIMEOUT'], [os.environ['REAL_TIMEOUT'],*sys.argv[1:]])
'''


class FlashGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT/'out').mkdir(exist_ok=True)
        cls.directory = tempfile.TemporaryDirectory(prefix='flash-guard-test-', dir=ROOT/'out')
        cls.output = Path(cls.directory.name)
        (cls.output/'images').mkdir()
        (cls.output/'images/rootfs.img').write_bytes(b'fixture-only; not a filesystem')
        with (cls.output/'images/boot.img').open('wb') as file:
            file.truncate(1073741824)  # Sparse fixture, no actual 1 GiB allocation.
        cls.layout = json.loads((ROOT/'profiles/nabu/tablet-layout.example.json').read_text())
        cls.layout.update(root_filesystem_uuid='00000000-0000-4000-8000-000000000001',
                          boot_filesystem_uuid='00000000-0000-4000-8000-000000000002',
                          esp_filesystem_uuid='0000-0003')
        (cls.output/'layout.json').write_text(json.dumps(cls.layout))
        with patch.object(nabu_images, 'kernel_input', return_value=cls.output/'absent-kernel'):
            cls.receipt = nabu_images.create(cls.output, cls.layout)
        cls.bin = cls.output/'bin'
        cls.bin.mkdir()
        executable = cls.bin/'fastboot'
        executable.write_text('#!'+sys.executable+'\n'+FAKE)
        executable.chmod(0o755)
        cls.real_timeout = shutil.which('timeout')
        assert cls.real_timeout
        wrapper = cls.bin/'timeout'
        wrapper.write_text('#!'+sys.executable+'\n'+TIMEOUT)
        wrapper.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.log = self.output/'fastboot-calls.jsonl'
        self.log.unlink(missing_ok=True)
        self.timeout_log = self.output/'timeout-calls.jsonl'
        self.timeout_log.unlink(missing_ok=True)
        (self.output/'image-receipt.json').write_text(json.dumps(self.receipt))

    def flash(self, **extra):
        env = {**os.environ, 'PATH': str(self.bin)+os.pathsep+os.environ['PATH'],
               'NABU_LAYOUT': str(self.output/'layout.json'), 'FAKE_FASTBOOT_LOG': str(self.log),
               'NABU_FASTBOOT_BIN':str(self.bin/'fastboot'), 'NABU_FASTBOOT_REBOOT_TIMEOUT':'600',
               'FAKE_TIMEOUT_LOG':str(self.timeout_log), 'REAL_TIMEOUT':self.real_timeout, **extra}
        return subprocess.run(['bash', str(ROOT/'scripts/flash-tablet-install.sh'),
                               '--flash', '--output', str(self.output)],
                              env=env, capture_output=True, text=True, timeout=30)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_only_root_and_boot_are_written_after_successful_checks(self):
        result = self.flash()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        writes = [args[args.index('flash')+1] for args in calls if 'flash' in args]
        self.assertEqual(writes, ['cust', 'userdata'])
        self.assertEqual(calls[-1][-1], 'reboot')

    def test_wrong_product_prevents_every_write(self):
        result = self.flash(FAKE_PRODUCT='another-board')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('flash' in args or 'reboot' in args for args in self.calls()))

    def test_preserved_esp_geometry_mismatch_prevents_every_write(self):
        result = self.flash(FAKE_WRONG_SIZE='partition-size:rawdump')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('flash' in args or 'reboot' in args for args in self.calls()))

    def test_mixed_image_receipt_fails_before_device_access(self):
        receipt = json.loads(json.dumps(self.receipt))
        receipt['artifacts']['rootfs.img']['sha256'] = '0'*64
        (self.output/'image-receipt.json').write_text(json.dumps(receipt))
        result = self.flash()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_failed_boot_write_stops_before_root_or_reboot(self):
        result = self.flash(FAKE_FAIL_PARTITION='cust')
        self.assertNotEqual(result.returncode,0)
        writes=[args[args.index('flash')+1] for args in self.calls() if 'flash' in args]
        self.assertEqual(writes,['cust'])
        self.assertFalse(any(args[-1:] == ['reboot'] for args in self.calls()))

    def test_reboot_has_a_minutes_long_configurable_budget(self):
        for budget in ('600','900'):
            result = self.flash(NABU_FASTBOOT_REBOOT_TIMEOUT=budget)
            self.assertEqual(result.returncode,0,result.stderr)
            calls=[json.loads(line) for line in self.timeout_log.read_text().splitlines()]
            reboot=[args for args in calls if args[-1:] == ['reboot']][-1]
            self.assertEqual(reboot[:3],['-k','2',budget])

    def test_unacknowledged_reboot_does_not_repeat_flashes(self):
        result = self.flash(FAKE_REBOOT_EXIT='124')
        self.assertEqual(result.returncode,124)
        self.assertIn('Both flash commands acknowledged',result.stdout)
        self.assertIn('Reboot is not confirmed',result.stderr)
        self.assertIn('inspect before forcing Power',result.stderr)
        writes=[args[args.index('flash')+1] for args in self.calls() if 'flash' in args]
        self.assertEqual(writes,['cust','userdata'])
        self.assertEqual(sum(args[-1:] == ['reboot'] for args in self.calls()),1)

    def test_short_reboot_budget_is_rejected_before_device_access(self):
        result = self.flash(NABU_FASTBOOT_REBOOT_TIMEOUT='15')
        self.assertEqual(result.returncode,2)
        self.assertEqual(self.calls(),[])
