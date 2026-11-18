import hashlib
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import nabu_kernel


class KernelSelection(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.input = self.root/'out/kernel'
        self.input.mkdir(parents=True)
        (self.root/'kernel').mkdir()
        (self.root/'kernel/debug.cmdline').write_bytes((ROOT/'kernel/debug.cmdline').read_bytes())
        self.profile = {}
        self.release = '7.3.4-nabu-debug2+'

    def select(self):
        (self.input/'sources.json').write_text(json.dumps(self.profile))
        (self.input/'kernelrelease').write_text(self.release+'\n')
        return nabu_kernel.select(self.input, self.root)

    def normal(self, extra=None):
        data = extra or (ROOT/'kernel/normal.cmdline').read_text()
        (self.root/'kernel/normal.cmdline').write_text(data)
        self.profile = {'boot': {'mode': 'normal', 'cmdline': {
            'path': 'kernel/normal.cmdline', 'sha256': hashlib.sha256(data.encode()).hexdigest()}}}
        self.release = '7.3.4-nabu1+'

    def test_legacy_debug_and_normal_use_distinct_slots_with_matching_arguments(self):
        debug = self.select()
        self.assertEqual(debug['slot'], 'debug')
        self.assertIn('initcall_debug', debug['extra_cmdline'].split())
        self.normal()
        normal = self.select()
        self.assertEqual(normal['slot'], 'local')
        self.assertEqual(normal['package'], 'linux-nabu-local')
        self.assertEqual(normal['uki'], 'arch-linux-nabu-local.efi')
        self.assertNotIn('initcall_debug', normal['extra_cmdline'].split())
        self.assertEqual(nabu_kernel.commandline('root=PARTLABEL=userdata\n', normal).split(),
                         ['root=PARTLABEL=userdata', *normal['extra_cmdline'].split()])

    def test_mainline_rc_and_stable_versions_keep_slot_separation(self):
        for version in ('7.4-rc3', '7.4.0-rc3', '7.4.0', '7.5-rc1'):
            with self.subTest(version=version):
                self.normal()
                self.release = version+'-nabu1+'
                self.assertEqual(self.select()['slot'], 'local')
                self.profile = {}
                self.release = version+'-nabu-debug7+'
                self.assertEqual(self.select()['slot'], 'debug')
        self.normal()
        for version in ('7.4-rc0', '7.4-rc', '7.4-rc3-extra'):
            self.release = version+'-nabu1+'
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.select()

    def test_package_version_maps_rc_without_changing_module_release(self):
        for release, expected in (('7.4.0-rc3-nabu1+', '7.4.0rc3'),
                                  ('7.4-rc3-nabu1+', '7.4rc3'),
                                  ('7.3.4-nabu1+', '7.3.4'),
                                  ('7.4-rc3-nabu-debug7+', '7.4rc3')):
            (self.input/'kernelrelease').write_text(release+'\n')
            result = subprocess.run(
                ['bash', '-c', 'source "$1"; printf "%s" "$pkgver"',
                 'check', str(ROOT/'kernel/PKGBUILD')],
                env={**os.environ, 'NABU_KERNEL_INPUT': str(self.input)},
                capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, expected)
            self.assertEqual((self.input/'kernelrelease').read_text().strip(), release)

    def test_profile_release_mismatch_and_changed_pin_are_rejected(self):
        self.normal()
        self.release = '7.3.4-nabu-debug6+'
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.select()
        self.release = '7.3.4-nabu1+'
        (self.root/'kernel/normal.cmdline').write_text('changed arguments')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.select()

    def test_normal_profile_cannot_override_board_or_enable_unsafe_verbose_console(self):
        for extra in ('earlycon=efifb,ram', 'keep_bootcon', 'initcall_debug',
                      'ignore_loglevel', 'root=elsewhere', 'rootfstype=btrfs', 'logo.nologo'):
            self.normal(extra+'\n')
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.select()

    def test_unsupported_mode_or_pin_outside_kernel_is_rejected(self):
        self.normal()
        self.profile['boot']['mode'] = 'custom'
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            self.select()
        self.normal()
        self.profile['boot']['cmdline']['path'] = 'out/kernel/kernelrelease'
        with self.assertRaisesRegex(ValueError, 'under kernel'):
            self.select()


if __name__ == '__main__':
    unittest.main()
