"""The public example must never silently become a flash target."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nabu_layout', ROOT/'scripts/nabu_layout.py')
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)


class LayoutValidation(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'layout.json'
        self.data = json.loads((ROOT/'profiles/nabu/tablet-layout.example.json').read_text())

    def load(self):
        self.path.write_text(json.dumps(self.data))
        with patch.dict(os.environ, {'NABU_LAYOUT': str(self.path)}):
            return layout.load_layout()

    def measured_values(self):
        self.data['root_filesystem_uuid'] = '00000000-0000-4000-8000-000000000001'
        self.data['boot_filesystem_uuid'] = '00000000-0000-4000-8000-000000000002'
        self.data['esp_filesystem_uuid'] = '0000-0003'

    def test_public_placeholder_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.load()

    def test_explicit_measured_layout_is_accepted(self):
        self.measured_values()
        self.assertEqual(self.load(), self.data)

    def test_unsupported_partition_or_size_is_rejected(self):
        self.measured_values()
        for key, value in [('root_partition', 'system'), ('boot_partition_bytes', 1),
                           ('root_partition_bytes', True), ('esp_filesystem_uuid', 'invalid')]:
            with self.subTest(key=key):
                saved = self.data[key]
                self.data[key] = value
                with self.assertRaises(SystemExit):
                    self.load()
                self.data[key] = saved

    def test_explicit_fat_boot_uses_short_uuid_without_relaxing_geometry(self):
        self.measured_values()
        self.data['boot_filesystem_type'] = 'vfat'
        with self.assertRaises(SystemExit):
            self.load()
        self.data['boot_filesystem_uuid'] = '1234-ABCD'
        self.assertEqual(self.load(), self.data)
        self.data['boot_partition_bytes'] *= 2
        with self.assertRaises(SystemExit):
            self.load()

    def test_tail_boot_build_proposal_is_accepted_but_never_flashable(self):
        self.measured_values()
        first = 1024
        original = 120*1024**3
        self.data.update(layout_profile='tail-boot-v1', layout_status='proposed',
                         boot_partition='nabu_boot', boot_filesystem_type='vfat',
                         boot_partition_bytes=4*1024**3, boot_filesystem_uuid='1234-ABCD',
                         root_start_lba=first, boot_start_lba=first+(original-4*1024**3)//4096,
                         original_root_partition_bytes=original,
                         root_partition_bytes=original-4*1024**3)
        self.assertEqual(self.load(),self.data)
        with self.assertRaisesRegex(ValueError,'offline-only'):
            layout.require_existing_flash_layout(self.data)
        self.data['boot_start_lba'] += 1
        with self.assertRaises(SystemExit):
            self.load()

    def test_missing_local_input_is_rejected(self):
        with patch.dict(os.environ, {'NABU_LAYOUT': str(self.path)}):
            with self.assertRaises(SystemExit):
                layout.load_layout()


if __name__ == '__main__':
    unittest.main()
