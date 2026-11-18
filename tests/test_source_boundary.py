from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SourceBoundary(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root/'tools').mkdir()
        shutil.copy2(ROOT/'tools/check-source.py', self.root/'tools/check-source.py')

    def check(self):
        return subprocess.run([sys.executable, str(self.root/'tools/check-source.py')], capture_output=True, text=True)

    def test_source_and_notices_are_allowed(self):
        (self.root/'helper.py').write_text('print("source fixture")\n')
        (self.root/'LICENSE').write_text('notice fixture\n')
        self.assertEqual(self.check().returncode, 0)

    def test_package_and_firmware_files_are_rejected_even_without_binary_magic(self):
        for name in ('firmware.mbn', 'firmware.fw', 'kernel.bin', 'boot.efi',
                     'kernel.pkg.tar.zst', 'package.rpm', 'package.deb'):
            path = self.root/name
            path.write_text('fixture\n')
            with self.subTest(name=name):
                result = self.check()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('binary/firmware artifact', result.stderr)
            path.unlink()

    def test_binary_content_cannot_hide_behind_an_unrecognized_suffix(self):
        path = self.root/'unexpected.data'
        for data in (b'\x7fELFfixture', b'MZfixture', b'prefix\0binary', b'\xff\xff'):
            path.write_bytes(data)
            result = self.check()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('binary content', result.stderr)

    def test_raw_logs_and_ssh_identity_files_are_not_public_source(self):
        for name in ('journal.log', 'capture.pcap', 'known_hosts', 'authorized_keys',
                     'id_ed25519.pub', 'ssh_host_rsa_key', 'shadow'):
            path = self.root/name
            path.write_text('non-secret fixture\n')
            with self.subTest(name=name):
                self.assertNotEqual(self.check().returncode, 0)
            path.unlink()

    def test_local_outputs_checkouts_and_evidence_are_outside_source_scope(self):
        for name in ('local', 'out', 'upstream'):
            directory = self.root/name
            directory.mkdir()
            (directory/'firmware.mbn').write_bytes(b'\x7fELFfixture')
            (directory/'raw.log').write_text('ignored local fixture\n')
        self.assertEqual(self.check().returncode, 0)

    def test_git_index_cannot_force_private_outputs_into_the_public_tree(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        (self.root/'README.md').write_text('source fixture\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'README.md'], check=True)
        (self.root/'out').mkdir()
        (self.root/'out/raw.log').write_text('local fixture\n')
        self.assertEqual(self.check().returncode, 0)
        subprocess.run(['git', '-C', str(self.root), 'add', '-f', 'out/raw.log'], check=True)
        result = self.check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('private/output path is tracked in Git', result.stderr)

    def test_markdown_is_checked_for_private_keys_and_lab_identifiers(self):
        path = self.root/'README.md'
        fixtures = ['-----BEGIN '+'OPENSSH PRIVATE KEY-----\nfixture\n',
                    '.'.join(('192', '168', '99', '42'))+'\n',
                    '/'.join(('', 'var', 'home', 'fixture-user', 'Projects', 'device'))+'\n']
        for value in fixtures:
            path.write_text(value)
            with self.subTest(value=value):
                self.assertNotEqual(self.check().returncode, 0)

    def test_overlay_usr_local_is_tracked_source_and_is_actually_scanned(self):
        path = self.root/'overlay/usr/local/libexec/helper'
        path.parent.mkdir(parents=True)
        path.write_text('#!/bin/sh\ntrue\n')
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), 'add', str(path)], check=True)
        self.assertEqual(self.check().returncode, 0)
        path.write_bytes(b'\x7fELFfixture')
        self.assertIn('binary content', self.check().stderr)


if __name__ == '__main__':
    unittest.main()
