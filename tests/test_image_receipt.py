import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import nabu_images
import nabu_paths
import nabu_image_options


class ImageReceipt(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        (self.output/'images').mkdir()
        (self.output/'images/rootfs.img').write_bytes(b'root image test')
        (self.output/'images/boot.img').write_bytes(b'bootimg!')
        self.layout = {'root_partition_bytes': 32, 'boot_partition_bytes': 8,
                       'root_filesystem_uuid': 'test-layout-one'}

    def create(self):
        with patch.object(nabu_images, 'kernel_input', return_value=self.output/'no-kernel'):
            return nabu_images.create(self.output, self.layout)

    def test_verified_pair_and_layout_are_bound(self):
        self.create()
        record = nabu_images.verify(self.output, self.layout)
        self.assertFalse(record['hardware_tested'])
        self.assertFalse(record['redistribution_approved'])

    def test_image_options_are_bound_and_missing_selection_is_rejected(self):
        selected = {**nabu_image_options.DEFAULT, 'display_controls': True}
        path = self.output/'image-options.json'
        path.write_bytes(nabu_image_options.encoded(selected))
        receipt = self.create()
        self.assertEqual(receipt['image_options'], selected)
        path.write_bytes(nabu_image_options.encoded(nabu_image_options.DEFAULT))
        with self.assertRaisesRegex(ValueError, 'recorded selection'):
            nabu_images.verify(self.output, self.layout)
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            nabu_images.verify(self.output, self.layout)

    def test_changed_option_snapshot_is_rejected_before_creating_receipt(self):
        (self.output/'image-options.json').write_bytes(nabu_image_options.encoded(nabu_image_options.DEFAULT))
        (self.output/'build-parameters.json').write_text(json.dumps({'image_options_sha256': '0'*64}))
        with self.assertRaisesRegex(ValueError, 'build input receipt'):
            self.create()
        self.assertFalse((self.output/'image-receipt.json').exists())

    def test_same_size_image_mutation_is_rejected(self):
        self.create()
        (self.output/'images/boot.img').write_bytes(b'changed!')
        with self.assertRaisesRegex(ValueError, 'differs from verified receipt'):
            nabu_images.verify(self.output, self.layout)

    def test_other_devices_equal_size_layout_is_rejected(self):
        self.create()
        other = {**self.layout, 'root_filesystem_uuid': 'test-layout-two'}
        with self.assertRaisesRegex(ValueError, 'different measured layout'):
            nabu_images.verify(self.output, other)

    def test_path_in_receipt_cannot_select_another_file(self):
        record = self.create()
        record['artifacts']['../other.img'] = record['artifacts'].pop('rootfs.img')
        (self.output/'image-receipt.json').write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'exactly rootfs.img'):
            nabu_images.verify(self.output, self.layout)

    def test_root_larger_than_partition_is_rejected(self):
        self.layout['root_partition_bytes'] = 1
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            self.create()


class BuildPaths(unittest.TestCase):
    def test_output_cannot_escape_project_or_out(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'project'
            root.mkdir()
            with patch.object(nabu_paths, 'PROJECT', root):
                for name in ('../outside', 'scripts/result'):
                    with patch.dict('os.environ', {'NABU_OUTPUT_REL': name}):
                        with self.assertRaises(ValueError): nabu_paths.output_dir()
                (root/'out').symlink_to(Path(directory)/'outside')
                with patch.dict('os.environ', {'NABU_OUTPUT_REL': 'out/run'}):
                    with self.assertRaises(ValueError): nabu_paths.output_dir()
