from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from nabu_image_boot import open_image
from nabu_filesystems import build_boot_fat


@unittest.skipUnless(all(shutil.which(n) for n in ('mkfs.fat', 'mcopy', 'blkid')),
                     'FAT fixture tools are required')
class FatReader(unittest.TestCase):
    def test_fat16_and_fat32_readback_missing_files_and_identity(self):
        for bits, size in ((16, 16*1024**2), (32, 64*1024**2)):
            with self.subTest(bits=bits), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                image = root/'fat.img'
                with image.open('wb') as f:
                    f.truncate(size)
                subprocess.run(['mkfs.fat','-F',str(bits),'-S','512','-s','1',
                                '-i','1234ABCD',str(image)], check=True, capture_output=True)
                tree = root/'EFI'; tree.mkdir()
                (tree/'limine.conf').write_text('one kernel\n')
                (tree/'empty').touch()
                subprocess.run(['mcopy','-i',str(image),'-s',str(tree),'::/'],check=True)
                scratch = root/'scratch'; scratch.mkdir()
                reader = open_image(image, scratch)
                self.assertEqual(reader.filesystem_uuid(), '1234-ABCD')
                self.assertEqual(reader.read_file('/EFI/limine.conf').read_text(), 'one kernel\n')
                self.assertEqual(reader.stats(['/EFI'])['/EFI'][0], 'directory')
                self.assertFalse(reader.exists('/EFI/missing'))
                with self.assertRaises(ValueError): reader.read_file('/EFI/empty')
                self.assertEqual(reader.read_file('/EFI/empty', allow_empty=True).read_bytes(), b'')
                with self.assertRaises(ValueError): reader.read_file('/../escape')


@unittest.skipUnless(all(shutil.which(n) for n in ('mkfs.fat', 'mcopy', 'blkid', 'fsck.fat')),
                     'FAT fixture tools are required')
class FatBuilder(unittest.TestCase):
    def test_4k_fat32_materializes_known_file_alias(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tree = root/'tree'; tree.mkdir()
            (tree/'dtb-release').write_bytes(b'device tree')
            (tree/'dtb-linux-nabu').symlink_to('dtb-release')
            image = root/'boot.img'
            build_boot_fat(tree, image, '1234-ABCD', 512*1024**2)
            scratch = root/'scratch'; scratch.mkdir()
            reader = open_image(image, scratch)
            self.assertEqual(reader.read_file('/dtb-linux-nabu').read_bytes(), b'device tree')
            self.assertTrue((tree/'dtb-linux-nabu').is_symlink())

    def test_escaping_links_and_case_collisions_fail_before_format(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); tree = root/'tree'; tree.mkdir()
            (root/'outside').write_text('private')
            alias = tree/'alias'; alias.symlink_to('../outside')
            with self.assertRaisesRegex(ValueError, 'leaves'):
                build_boot_fat(tree, root/'boot.img', '1234-ABCD', 512*1024**2)
            self.assertFalse((root/'boot.img').exists())
            alias.unlink()
            (tree/'Kernel').touch(); (tree/'kernel').touch()
            with self.assertRaisesRegex(ValueError, 'Case-colliding'):
                build_boot_fat(tree, root/'boot.img', '1234-ABCD', 512*1024**2)
