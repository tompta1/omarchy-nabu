"""OS-05: inspect real disposable ext4 fixtures without mounts or a tablet."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import nabu_image_boot as images
from nabu_grub import menu_config, kernel_entries
from nabu_limine import default_config
from test_inspect_nabu_uki import fixture


@unittest.skipUnless(all(shutil.which(name) for name in ('mke2fs', 'debugfs', 'dumpe2fs')),
                     'ext4 image tools are needed for filesystem fixtures')
class ImageBoot(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.work = Path(self.directory.name)
        self.counter = 0
        self.layout = {'root_filesystem_uuid': '11111111-1111-4111-8111-111111111111',
                       'boot_filesystem_uuid': '22222222-2222-4222-8222-222222222222'}

    def make(self, primary='local', mutate=None, filesystem='ext4', manager='grub'):
        self.counter += 1
        case = self.work/str(self.counter)
        root = case/'root'; root.mkdir(parents=True)
        boot = case/'boot'; boot.mkdir()
        compiled = '7.3.4-nabu1+' if primary == 'local' else '7.3.4-nabu-debug2+'
        stock = '7.3.3-3-krjy'

        def write(tree, relative, data):
            path = tree/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data.encode() if isinstance(data, str) else data)

        if manager == 'grub':
            write(boot, 'grub2/grub.cfg', menu_config(self.layout, {}, primary))
            write(root, 'boot/grub2/grub.cfg',
                  'search --no-floppy --fs-uuid --set=omarchy_boot '+self.layout['boot_filesystem_uuid']+'\n'
                  'configfile ($omarchy_boot)/grub2/grub.cfg\n')
        for filename, release in (('arch-linux-nabu-'+primary+'.efi', compiled),
                                  ('arch-linux-nabu-stock.efi', stock)):
            cmdline = ('root=PARTLABEL=userdata rootfstype='+filesystem+
                       (' rootflags=subvol=@' if filesystem == 'btrfs' else '')).encode()
            write(boot, 'EFI/arch/'+filename, fixture(cmdline=cmdline,release=release.encode()))
            write(boot, 'vmlinux-'+release, b'kernel')
            write(boot, 'dtb-'+release, b'dtb')
            base = 'usr/lib/modules/'+release+'/'
            write(root, base+'modules.dep', 'kernel/example.ko: kernel/dep.ko\nkernel/dep.ko:\n')
            write(root, base+'kernel/example.ko', b'module fixture')
            write(root, base+'kernel/dep.ko', b'dependency fixture')
        write(root, 'usr/lib/modules/'+compiled+'/config', 'CONFIG_ARM64=y\n')
        write(root, 'etc/kernel/uki-nabu-'+primary+'.conf', '[UKI]\nDeviceTree=/boot/dtb-'+compiled+'\n')
        write(root, 'etc/kernel/uki.conf', '[UKI]\nDeviceTree=/boot/dtb-linux-nabu\n')
        (boot/'dtb-linux-nabu').symlink_to('dtb-'+stock)
        if manager == 'limine':
            target = boot/'EFI/Linux/omarchy.efi'
            target.parent.mkdir(parents=True)
            (boot/('EFI/arch/arch-linux-nabu-'+primary+'.efi')).rename(target)
            shutil.rmtree(boot/'EFI/arch')
            write(boot, 'limine.conf', default_config(cmdline.decode(), slot='local'))
        if mutate:
            mutate(root, boot, compiled, stock)
        result = []
        for name, tree, key in (('root', root, 'root_filesystem_uuid'),
                                ('boot', boot, 'boot_filesystem_uuid')):
            image = case/(name+'.img')
            if name == 'root' and filesystem == 'btrfs':
                from nabu_filesystems import build
                top = case/'btrfs-stage'; top.mkdir()
                tree.rename(top/'@')
                build(top,image,self.layout[key],8*1024**3)
                result.append(image)
                continue
            if name == 'boot' and manager == 'limine':
                from nabu_filesystems import build_boot_fat
                build_boot_fat(tree, image, self.layout[key], 512*1024**2)
                result.append(image)
                continue
            with image.open('wb') as stream:
                stream.truncate(8*1024*1024)
            subprocess.run(['mke2fs', '-q', '-F', '-t', 'ext4', '-U', self.layout[key],
                            '-d', str(tree), str(image)], check=True, capture_output=True)
            result.append(image)
        return result

    def inspect(self, pair, primary='local', layout=None):
        return images.inspect_images(*pair, layout or self.layout, {}, primary)

    def test_debug_and_normal_primary_match_payloads_modules_and_stock_alias(self):
        for primary in ('debug', 'local'):
            with self.subTest(primary=primary):
                reports = self.inspect(self.make(primary), primary)
                self.assertEqual([row['uki'] for row in reports], list(kernel_entries(primary).values()))
                self.assertEqual([row['module_files'] for row in reports], [2, 2])
                self.assertTrue(all(row['kernel_dtb_match'] for row in reports))

    @unittest.skipUnless(all(shutil.which(n) for n in ('mkfs.fat', 'mcopy', 'blkid', 'fsck.fat')),
                         'FAT tools required')
    def test_limine_fat_menu_payloads_and_modules_without_grub(self):
        self.layout['boot_filesystem_uuid'] = '1234-ABCD'
        pair = self.make(manager='limine')
        reports = images.inspect_images(*pair, self.layout, {}, 'local', 'limine')
        self.assertEqual([row['uki'] for row in reports], ['omarchy.efi'])
        self.assertEqual(reports[0]['module_files'], 2)
        wrong = {**self.layout, 'boot_filesystem_uuid': '1234-ABCE'}
        with self.assertRaisesRegex(ValueError, 'filesystem UUID differs'):
            images.inspect_images(*pair, wrong, {}, 'local', 'limine')
        def mutate(root, boot, compiled, stock):
            (boot/'limine.conf').write_text(default_config().replace('omarchy.efi', 'missing.efi'))
        with self.assertRaisesRegex(ValueError, 'Limine menu'):
            images.inspect_images(*self.make(manager='limine', mutate=mutate),
                                  self.layout, {}, 'local', 'limine')

    @unittest.skipUnless(all(shutil.which(n) for n in
                         ('mkfs.fat', 'mcopy', 'blkid', 'fsck.fat', 'btrfs', 'mkfs.btrfs')),
                         'Btrfs and FAT tools required')
    def test_btrfs_root_and_fat_boot_limine_payloads(self):
        result = subprocess.run(['mkfs.btrfs', '--help'], capture_output=True, text=True)
        if '--subvol' not in result.stdout+result.stderr:
            self.skipTest('mkfs.btrfs needs rootdir subvolume support')
        self.layout['boot_filesystem_uuid'] = '1234-ABCD'
        pair = self.make(manager='limine', filesystem='btrfs')
        reports = images.inspect_images(*pair, self.layout, {}, 'local', 'limine')
        self.assertEqual(reports[0]['release'], '7.3.4-nabu1+')
        self.assertTrue(reports[0]['kernel_dtb_match'])
        def mutate(root, boot, compiled, stock):
            (boot/('dtb-'+compiled)).write_bytes(b'wrong device tree')
        pair = self.make(manager='limine', filesystem='btrfs', mutate=mutate)
        with self.assertRaisesRegex(ValueError, 'Embedded DTB differs'):
            images.inspect_images(*pair, self.layout, {}, 'local', 'limine')

    def test_real_filesystem_uuid_mismatch_is_rejected(self):
        pair = self.make()
        for key in self.layout:
            wrong = {**self.layout, key: '33333333-3333-4333-8333-333333333333'}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'filesystem UUID differs'):
                self.inspect(pair, layout=wrong)

    def test_changed_menu_and_underlying_root_redirect_are_rejected(self):
        for change in ('menu', 'redirect'):
            def mutate(root, boot, compiled, stock):
                if change == 'menu':
                    path = boot/'grub2/grub.cfg'
                    path.write_text(path.read_text()+"menuentry 'unreviewed' { reboot; }\n")
                else:
                    (root/'boot/grub2/grub.cfg').write_text('configfile /unreviewed/grub.cfg\n')
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.inspect(self.make(mutate=mutate))

    def test_mismatched_kernel_dtb_and_configured_dtb_are_rejected(self):
        for change in ('kernel', 'dtb', 'config'):
            def mutate(root, boot, compiled, stock):
                if change == 'config':
                    (root/'etc/kernel/uki-nabu-local.conf').write_text('[UKI]\nDeviceTree=/boot/dtb-'+stock+'\n')
                else:
                    path = boot/(('vmlinux-' if change == 'kernel' else 'dtb-')+compiled)
                    path.write_bytes(b'mismatched fixture')
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.inspect(self.make(mutate=mutate))

    def test_versioned_root_kernel_and_dtb_match_the_uki(self):
        for corrupt in (None,'vmlinuz','nabu.dtb'):
            def mutate(root,boot,compiled,stock):
                base=root/'usr/lib/modules'/compiled
                (base/'vmlinuz').write_bytes(b'kernel')
                (base/'nabu.dtb').write_bytes(b'dtb')
                (root/'etc/kernel/uki-nabu-local.conf').write_text(
                    '[UKI]\nDeviceTree=/usr/lib/modules/'+compiled+'/nabu.dtb\n')
                if corrupt:
                    (base/corrupt).write_bytes(b'wrong payload')
            with self.subTest(corrupt=corrupt):
                if corrupt:
                    with self.assertRaisesRegex(ValueError,'Root .* differs from UKI'):
                        self.inspect(self.make(mutate=mutate))
                else:
                    self.assertTrue(self.inspect(self.make(mutate=mutate))[0]['kernel_dtb_match'])

    def test_missing_metadata_module_and_unsafe_module_dependency_are_rejected(self):
        for change in ('metadata', 'module', 'unsafe'):
            def mutate(root, boot, compiled, stock):
                base = root/'usr/lib/modules'/compiled
                if change == 'unsafe':
                    (base/'modules.dep').write_text('kernel/example.ko: ../outside.ko\n')
                else:
                    (base/('modules.dep' if change == 'metadata' else 'kernel/dep.ko')).unlink()
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.inspect(self.make(mutate=mutate))

    def test_unreviewed_stock_symlink_is_rejected(self):
        def mutate(root, boot, compiled, stock):
            link = boot/'dtb-linux-nabu'
            link.unlink()
            link.symlink_to('../unreviewed/dtb')
        with self.assertRaisesRegex(ValueError, 'Stock DTB alias'):
            self.inspect(self.make(mutate=mutate))

    def test_debugfs_missing_file_success_status_cannot_pass_reader_checks(self):
        pair = self.make()
        temporary = self.work/'reader'; temporary.mkdir()
        reader = images.Ext4Image(pair[0], temporary)
        for action in (lambda: reader.read_file('/missing'), lambda: reader.stats(['/missing'])):
            with self.assertRaisesRegex(ValueError, 'Missing'):
                action()
        for path in ('/../outside', '/path"\nstat /elsewhere', '/double//separator'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'Unsafe'):
                reader.read_file(path)

    @unittest.skipUnless(shutil.which('btrfs') and shutil.which('mkfs.btrfs'), 'Btrfs tools required')
    def test_btrfs_root_and_ext4_boot_match_both_kernel_entries(self):
        help_result = subprocess.run(['mkfs.btrfs','--help'],capture_output=True,text=True)
        help_text = help_result.stdout+help_result.stderr
        if '--subvol' not in help_text:
            self.skipTest('mkfs.btrfs needs rootdir subvolume support')
        def mutate(root, boot, compiled, stock):
            (root/'home/tablet').mkdir(parents=True)
            (root/'home/tablet/example').write_text('separate home fixture\n')
            (root/'etc/resolv.conf').symlink_to('/run/systemd/resolve/stub-resolv.conf')
        pair = self.make(filesystem='btrfs',mutate=mutate)
        reports = self.inspect(pair)
        self.assertEqual([row['module_files'] for row in reports],[2,2])
        scratch = self.work/'btrfs-reader'; scratch.mkdir()
        reader = images.open_image(pair[0],scratch)
        self.assertEqual(reader.filesystem,'btrfs')
        self.assertEqual(reader.read_file('/home/tablet/example').read_text(),'separate home fixture\n')
        self.assertEqual(reader.link_target('/etc/resolv.conf'),'/run/systemd/resolve/stub-resolv.conf')
        self.assertFalse(reader.exists('/missing'))
        self.assertIn('tablet',reader.listdir('/home'))
        self.assertIn('usr',reader.listdir('/'))
        wrong = {**self.layout,'root_filesystem_uuid':'33333333-3333-4333-8333-333333333333'}
        with self.assertRaisesRegex(ValueError,'filesystem UUID differs'):
            self.inspect(pair,layout=wrong)


if __name__ == '__main__':
    unittest.main()
