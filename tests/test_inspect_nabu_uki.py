import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('inspect_uki', Path(__file__).resolve().parents[1] / 'scripts/inspect-nabu-uki.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(cmdline=b'root=PARTLABEL=userdata rootfstype=ext4', release=b'7.3.4-nabu', efi=False):
    sections = {b'.linux': b'kernel', b'.initrd': b'initrd', b'.dtb': b'dtb',
                b'.cmdline': cmdline+b'\0', b'.uname': release+b'\0'}
    optional = 240 if efi else 0
    data = bytearray(1024 if efi else 512)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 64)
    data[64:68] = b'PE\0\0'
    struct.pack_into('<HH', data, 68, 0xaa64, len(sections))
    if efi:
        struct.pack_into('<H', data, 84, optional)
        struct.pack_into('<H', data, 88, 0x20b)
        struct.pack_into('<H', data, 156, 10)
    for index, (name, content) in enumerate(sections.items()):
        entry = 88 + optional + index * 40
        data[entry:entry+len(name)] = name
        struct.pack_into('<IIII', data, entry+8, len(content), 0, len(content), len(data))
        data.extend(content)
    return data


class InspectionTest(unittest.TestCase):
    def check_data(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.efi'
            path.write_bytes(data)
            return module.inspect(path)

    def test_valid(self):
        self.assertEqual(self.check_data(fixture()), '7.3.4-nabu')

    def test_truncated_section_and_wrong_architecture(self):
        damaged = fixture()
        struct.pack_into('<I', damaged, 88+20, len(damaged)+1)
        with self.assertRaises(ValueError):
            self.check_data(damaged)
        damaged = fixture()
        struct.pack_into('<H', damaged, 68, 0x8664)
        with self.assertRaises(ValueError):
            self.check_data(damaged)

    def test_root_and_missing_section(self):
        damaged = fixture().replace(b'root=PARTLABEL=userdata', b'root=PARTLABEL=wrongxxx')
        with self.assertRaises(ValueError):
            self.check_data(damaged)

    def test_conflicting_root_type_and_unsafe_console(self):
        for suffix in (b' root=/dev/wrong', b' root=PARTLABEL=userdata',
                       b' rootfstype=btrfs', b' keep_bootcon',
                       b' earlycon=efifb,keep', b' bad\0arg'):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.check_data(fixture(b'root=PARTLABEL=userdata rootfstype=ext4'+suffix))
        with self.assertRaises(ValueError):
            self.check_data(fixture(b'root=PARTLABEL=userdata'))

    def test_overlapping_and_short_virtual_payload(self):
        damaged = fixture()
        struct.pack_into('<I', damaged, 88+40+20, 512)
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            self.check_data(damaged)

        damaged = fixture()
        struct.pack_into('<I', damaged, 88+8, 999)
        with self.assertRaisesRegex(ValueError, 'Truncated UKI'):
            self.check_data(damaged)
        damaged = fixture()
        damaged[88:96] = b'.other\0\0'
        with self.assertRaises(ValueError):
            self.check_data(damaged)

    def test_btrfs_requires_the_selected_subvolume_and_exact_filesystem(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'uki.efi'
            base = b'root=PARTLABEL=userdata rootfstype=btrfs'
            path.write_bytes(fixture(base+b' rootflags=subvol=@'))
            self.assertEqual(module.inspect(path,filesystem='btrfs'),'7.3.4-nabu')
            with self.assertRaises(ValueError):
                module.inspect(path)
            for suffix in (b'',b' rootflags=subvol=@home',b' rootflags=subvol=@ rootflags=subvol=@home'):
                path.write_bytes(fixture(base+suffix))
                with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                    module.inspect(path,filesystem='btrfs')


if __name__ == '__main__':
    unittest.main()
