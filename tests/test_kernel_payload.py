"""Package the selected EFI payload without installing anything on the host."""
import io
from itertools import product
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class KernelPayload(unittest.TestCase):
    def test_raw_and_zboot_packages_use_the_configured_payload(self):
        for compressed, manager in product((False, True), ('grub', 'limine')):
            with self.subTest(compressed=compressed, manager=manager), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root/'input'; source.mkdir()
                release = '7.4.0-rc3-nabu2+'
                for name, data in {'kernelrelease': (release+'\n').encode(), 'Image': b'raw kernel',
                                   'Image.gz': b'gzip auxiliary', 'vmlinuz.efi': b'EFI decompressor',
                                   'sm8150-xiaomi-nabu.dtb': b'device tree',
                                   'config': b'CONFIG_EFI_ZBOOT=y\n' if compressed else b'# CONFIG_EFI_ZBOOT is not set\n'}.items():
                    (source/name).write_bytes(data)
                generated = ('modules.alias', 'modules.dep', 'modules.devname', 'modules.softdep',
                             'modules.symbols', 'modules.weakdep', 'modules.builtin.bin')
                retained = ('dummy.ko', 'modules.order', 'modules.builtin', 'modules.builtin.modinfo')
                with tarfile.open(source/'modules.tar', 'w') as archive:
                    for name in generated + retained:
                        member = tarfile.TarInfo('usr/lib/modules/'+release+'/'+name)
                        member.size = 6
                        archive.addfile(member, io.BytesIO(b'module'))
                package = root/'package'; package.mkdir()
                subprocess.run(['bash', '-ec',
                                'bsdtar() { tar "$@"; }; source "$1"; package',
                                'package-check', str(ROOT/'kernel/PKGBUILD')], check=True,
                               env={**os.environ, 'NABU_KERNEL_INPUT': str(source),
                                    'NABU_BOOT_MANAGER': manager, 'pkgdir': str(package)}, capture_output=True)
                expected = b'EFI decompressor' if compressed else b'raw kernel'
                modules = package/'usr/lib/modules'/release
                for name in generated:
                    self.assertFalse((modules/name).exists())
                for name in retained:
                    self.assertTrue((modules/name).is_file())
                self.assertEqual((modules/'vmlinuz').read_bytes(), expected)
                self.assertEqual((modules/'nabu.dtb').read_bytes(), b'device tree')
                self.assertIn('DeviceTree=/usr/lib/modules/'+release+'/nabu.dtb',
                              (package/'etc/kernel/uki-nabu-debug.conf').read_text())
                preset = (package/'etc/mkinitcpio.d/linux-nabu-debug.preset').read_text()
                if manager == 'grub':
                    self.assertEqual((package/'boot'/('vmlinux-'+release)).read_bytes(), expected)
                    self.assertEqual((package/'boot'/('dtb-'+release)).read_bytes(), b'device tree')
                    self.assertIn('--kernelimage /boot/vmlinux-'+release, preset)
                else:
                    self.assertFalse((package/'boot').exists())
                    self.assertIn('--kernelimage /usr/lib/modules/'+release+'/vmlinuz', preset)


    def test_limine_package_can_coexist_with_the_bootstrap_linux_provider(self):
        # The bootstrap linux-nabu package conflicts with the virtual name linux.
        # Advertising it before explicit removal would trigger an early conflict.
        for manager in ('grub','limine'):
            result=subprocess.run(['bash','-ec',
                'source "$1"; printf "%s\n" "${provides[@]-}"',
                'metadata-check',str(ROOT/'kernel/PKGBUILD')],check=True,
                capture_output=True,text=True,env={**os.environ,'NABU_BOOT_MANAGER':manager,
                                                  'NABU_KERNEL_INPUT':''})
            provides={line.split('=',1)[0] for line in result.stdout.splitlines() if line}
            self.assertNotIn('linux',provides)
            self.assertEqual('linux-nabu' in provides,manager=='limine')
