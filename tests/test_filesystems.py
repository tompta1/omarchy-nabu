"""OS-11: Btrfs mounts must agree with the boot policy and preserve board data."""
from pathlib import Path
import sys
import struct
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from nabu_filesystems import fstab,policy,fat_geometry


class Filesystems(unittest.TestCase):
    def test_subvolume_mounts_and_growfs_are_explicit(self):
        layout={kind+'_filesystem_uuid':kind+'-fixture' for kind in ('root','boot','esp')}
        entries=[line.split() for line in fstab(layout).splitlines()]
        by_mount={entry[1]:entry for entry in entries}
        for subvolume,mount in policy()['subvolumes'].items():
            row=by_mount[mount]
            self.assertEqual(row[0],'UUID=root-fixture')
            self.assertEqual(row[2],'btrfs')
            self.assertIn('subvol='+subvolume,row[3].split(','))
            self.assertIn('compress=zstd',row[3].split(','))
            self.assertEqual('x-systemd.growfs' in row[3].split(','),mount=='/')
            self.assertEqual(row[4:],['0','0'])
        self.assertEqual(by_mount['/boot'][2],'ext4')
        self.assertEqual(by_mount['/boot/efi'][2],'vfat')
        self.assertIn('ro',by_mount['/mnt/vendor/persist'][3].split(','))
        cmdline=(ROOT/'profiles/nabu/kernel.cmdline').read_text().split()
        self.assertIn('rootfstype=btrfs',cmdline)
        self.assertEqual([arg for arg in cmdline if arg.startswith('rootflags=')],['rootflags=subvol=@'])


if __name__=='__main__':
    unittest.main()


class FatGeometry(unittest.TestCase):
    def bpb(self):
        data = bytearray(4096)
        struct.pack_into('<H', data, 11, 4096)
        data[13] = 1
        struct.pack_into('<H', data, 14, 32)
        data[16] = 2
        struct.pack_into('<I', data, 32, 32768)
        return data

    def test_small_forced_fat32_is_not_portable(self):
        data = self.bpb()
        struct.pack_into('<I', data, 36, 32)
        with self.assertRaisesRegex(ValueError, 'cluster count'):
            fat_geometry(data)

    def test_valid_fat16_and_larger_fat32(self):
        data = self.bpb()
        struct.pack_into('<H', data, 17, 512)
        struct.pack_into('<H', data, 22, 16)
        self.assertEqual(fat_geometry(data)['fat_bits'], 16)
        data = self.bpb()
        struct.pack_into('<I', data, 32, 262144)
        struct.pack_into('<I', data, 36, 256)
        self.assertEqual(fat_geometry(data)['fat_bits'], 32)


class FatBootMount(unittest.TestCase):
    def test_explicit_fat_boot_mount_keeps_btrfs_root_and_persist(self):
        layout = {kind+'_filesystem_uuid': kind+'-fixture' for kind in ('root', 'boot', 'esp')}
        layout['boot_filesystem_type'] = 'vfat'
        rows = {line.split()[1]:line.split() for line in fstab(layout).splitlines()}
        self.assertEqual(rows['/boot'][2], 'vfat')
        self.assertEqual(rows['/efi'][2], 'vfat')
        self.assertNotIn('/boot/efi',rows)
        self.assertIn('umask=0077', rows['/boot'][3])
        self.assertEqual(rows['/'][2], 'btrfs')
        self.assertIn('ro', rows['/mnt/vendor/persist'][3])
