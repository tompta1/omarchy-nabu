import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import nabu_limine
from test_inspect_nabu_uki import fixture


def efi(machine=0xaa64, subsystem=10):
    data = bytearray(512)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 64)
    data[64:68] = b'PE\0\0'
    struct.pack_into('<H', data, 68, machine)
    struct.pack_into('<H', data, 88, 0x20b)
    struct.pack_into('<H', data, 156, subsystem)
    return data


class LimineBundle(unittest.TestCase):
    def test_bundle_preserves_inputs_and_does_not_replace_default(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            loader, grub = root/'limine.efi', root/'grub.efi'
            loader.write_bytes(efi())
            grub.write_bytes(efi()+b'retained config')
            result = nabu_limine.assemble(loader, grub, root/'bundle')
            self.assertFalse(result['hardware_validated'])
            self.assertEqual(len(result['files']), 3)
            self.assertNotIn('EFI/BOOT/BOOTAA64.EFI', result['files'])
            self.assertEqual(grub.read_bytes(), efi()+b'retained config')
            config = (root/'bundle/EFI/omarchy-nabu/limine/limine.conf').read_text()
            self.assertIn('protocol: efi', config)
            self.assertIn('grub-recovery.efi', config)
            self.assertEqual(json.loads((root/'bundle/manifest.json').read_text()), result)
            with self.assertRaises(ValueError):
                nabu_limine.assemble(loader, grub, root/'bundle')

    def test_reject_wrong_arch_subsystem_and_truncated_pe(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'bad.efi'
            for data in (efi(machine=0x8664), efi(subsystem=3), efi()[:90], b'broken'):
                path.write_bytes(data)
                with self.subTest(size=len(data)), self.assertRaises(ValueError):
                    nabu_limine.verify_arm64_efi(path)


class BootCapacity(unittest.TestCase):
    def test_update_peak_and_usage_limit_are_counted(self):
        result = nabu_limine.boot_budget(1000, 100, 50, 25, snapshot_limit=5)
        self.assertEqual(result['required_bytes'], 775)
        self.assertTrue(result['fits'])
        self.assertFalse(nabu_limine.boot_budget(900, 100, 50, 25, snapshot_limit=5)['fits'])
        self.assertFalse(nabu_limine.boot_budget(1000, 100, 50, 25, snapshot_limit=6)['fits'])

    def test_invalid_policy_is_rejected(self):
        for args in ((0, 1, 1, 1), (1000, -1, 1, 1), (1000, 1, True, 1)):
            with self.assertRaises(ValueError):
                nabu_limine.boot_budget(*args)


class DirectKernelBundle(unittest.TestCase):
    def test_kernel_entry_precedes_retained_grub(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = [root/name for name in ('limine.efi', 'grub.efi', 'kernel.efi')]
            for path in paths:
                path.write_bytes(efi())
            result = nabu_limine.assemble(paths[0], paths[1], root/'direct', paths[2])
            self.assertEqual(len(result['files']), 4)
            config = (root/'direct/EFI/omarchy-nabu/limine/limine.conf').read_text()
            self.assertLess(config.index('kernel-candidate.efi'), config.index('grub-recovery.efi'))
            self.assertIn('default_entry: 1', config)


class DefaultEsp(unittest.TestCase):
    def test_default_contains_one_kernel_and_same_loader_at_firmware_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            loader, uki = root/'limine.efi', root/'uki.efi'
            loader.write_bytes(efi()+b'one loader')
            uki.write_bytes(efi()+b'one kernel')
            result = nabu_limine.assemble_default(loader, uki, root/'esp')
            self.assertEqual(result['loader_versions'], 1)
            self.assertEqual(result['kernel_entries'], 1)
            self.assertEqual((root/'esp/EFI/BOOT/BOOTAA64.EFI').read_bytes(),
                             (root/'esp/EFI/fedora/shimaa64.efi').read_bytes())
            config = (root/'esp/EFI/BOOT/limine.conf').read_text()
            self.assertNotIn('GRUB', config)
            self.assertEqual(config.count('protocol: efi'), 1)
            self.assertIn('/EFI/Linux/omarchy.efi', config)
            self.assertFalse(any('grub' in name.lower() for name in result['files']))
            with self.assertRaises(ValueError):
                nabu_limine.assemble_default(loader, uki, root/'esp')


class SplitBootVolumes(unittest.TestCase):
    def test_loader_esp_cannot_shadow_the_boot_volume_menu(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            loader = root/'loader.efi'; loader.write_bytes(efi()+b'loader')
            cmdline = 'root=PARTLABEL=userdata rootfstype=btrfs rootflags=subvol=@ rw acpi=off'
            uki = root/'kernel.efi'; uki.write_bytes(fixture(cmdline=(cmdline+'\n').encode(), efi=True))
            result = nabu_limine.assemble_loader(loader, root/'esp')
            self.assertEqual(set(result['files']),
                             {'EFI/BOOT/BOOTAA64.EFI', 'EFI/fedora/shimaa64.efi'})
            self.assertFalse(list((root/'esp').rglob('limine.conf')))
            self.assertEqual(result['configuration_volume'], 'boot')
            boot = root/'boot'; boot.mkdir()
            nabu_limine.prepare_boot(uki, boot)
            self.assertEqual((boot/'EFI/Linux/omarchy.efi').read_bytes(), uki.read_bytes())
            menu = (boot/'limine.conf').read_text()
            self.assertIn('default_entry: Omarchy Nabu/linux-nabu-local\n', menu)
            self.assertIn('/+Omarchy Nabu\n  //linux-nabu-local\n    protocol: efi\n', menu)
            self.assertIn('cmdline: '+cmdline, menu)
            with self.assertRaises(ValueError):
                nabu_limine.prepare_boot(uki, boot)

    def test_boot_payload_cannot_follow_an_existing_efi_directory_link(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            uki = root/'kernel.efi'; uki.write_bytes(fixture(
                cmdline=b'root=PARTLABEL=userdata rootfstype=btrfs rootflags=subvol=@', efi=True))
            boot = root/'boot'; boot.mkdir()
            outside = root/'outside'; outside.mkdir()
            (boot/'EFI').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                nabu_limine.prepare_boot(uki, boot)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((boot/'limine.conf').exists())


class SnapshotCommandline(unittest.TestCase):
    def test_ext4_uki_cannot_be_staged_as_btrfs_snapshot_boot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            uki = root/'kernel.efi'; uki.write_bytes(fixture(efi=True))
            boot = root/'boot'; boot.mkdir()
            with self.assertRaisesRegex(ValueError, 'filesystem type'):
                nabu_limine.prepare_boot(uki, boot)
            self.assertEqual(list(boot.iterdir()), [])

    def test_menu_commandline_cannot_inject_another_entry(self):
        for value in ('', 'rw\n/other', 'rw\rpath: bad', 'rw\0other'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                nabu_limine.default_config(value)


class SnapshotInitramfs(unittest.TestCase):
    def test_missing_tooling_cannot_silently_replace_the_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root/'etc/mkinitcpio.conf.d/zz-nabu.conf'
            target.parent.mkdir(parents=True)
            target.write_text('baseline')
            with self.assertRaisesRegex(ValueError, 'marked isolated'):
                nabu_limine.install_snapshot_initramfs(root)
            (root/'.omarchy-nabu-build-root').touch()
            with self.assertRaisesRegex(ValueError, 'Missing snapshot recovery'):
                nabu_limine.install_snapshot_initramfs(root)
            self.assertEqual(target.read_text(), 'baseline')
            for name in ('usr/lib/initcpio/install/sd-btrfs-overlayfs',
                         'usr/lib/limine/overlayfs-setup',
                         'usr/lib/systemd/system/overlayfs-setup.service',
                         'usr/bin/limine-snapper-sync', 'usr/bin/limine-snapper-restore',
                         'usr/bin/limine-update', 'usr/bin/snapper'):
                path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.touch()
            nabu_limine.install_snapshot_initramfs(root)
            config = target.read_text()
            self.assertIn('filesystems sd-btrfs-overlayfs', config)
            self.assertNotIn(' btrfs-overlayfs ', config)
            hooks = next(line for line in config.splitlines() if line.startswith('HOOKS='))
            self.assertNotIn('autodetect', hooks)


class SnapshotInventory(unittest.TestCase):
    def test_missing_runtime_or_wrong_kernel_module_cannot_pass(self):
        release = '7.4.0-rc3-nabu1+'
        paths = ['usr/lib/limine/overlayfs-setup',
                 'usr/lib/systemd/system/overlayfs-setup.service',
                 'usr/lib/systemd/system/initrd.target.wants/overlayfs-setup.service',
                 'usr/bin/bash', 'usr/bin/btrfs', 'usr/bin/findmnt', 'usr/bin/mount',
                 'usr/lib/firmware/qcom/a630_sqe.fw', 'usr/lib/firmware/qcom/a640_gmu.bin',
                 'usr/lib/firmware/qcom/sm8150/xiaomi/nabu/a640_zap.mbn',
                 'usr/lib/modules/'+release+'/kernel/fs/overlayfs/overlay.ko.zst']
        config = 'CONFIG_BTRFS_FS=y\nCONFIG_OVERLAY_FS=m\n'
        nabu_limine.verify_snapshot_listing('\n'.join(paths), release, config)
        for missing in paths:
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                nabu_limine.verify_snapshot_listing('\n'.join(p for p in paths if p != missing),
                                                   release, config)
        with self.assertRaisesRegex(ValueError, 'OVERLAY_FS'):
            nabu_limine.verify_snapshot_listing('\n'.join(paths), '7.4.0-rc3-other', config)
        # A built-in OverlayFS needs no module file in the archive.
        nabu_limine.verify_snapshot_listing('\n'.join(paths[:-1]), release,
                                            'CONFIG_BTRFS_FS=y\nCONFIG_OVERLAY_FS=y\n')


class ImageBootStage(unittest.TestCase):
    def test_selected_payloads_are_staged_without_grub_or_fat_case_collision(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); source=root/'source'; (source/'boot/EFI/arch').mkdir(parents=True)
            release='7.4.0-rc3-nabu1+'
            uki=source/'boot/EFI/arch/arch-linux-nabu-debug-next.efi'
            uki.write_bytes(fixture(cmdline=b'root=PARTLABEL=userdata rootfstype=btrfs rootflags=subvol=@',
                                    release=release.encode(),efi=True))
            modules=source/'usr/lib/modules'/release; modules.mkdir(parents=True)
            (modules/'vmlinuz').write_bytes(b'kernel')
            dtb=modules/'nabu.dtb';dtb.write_bytes(b'dtb')
            (source/'boot/grub2').mkdir();(source/'boot/grub2/grub.cfg').write_text('historical')
            output=root/'boot'
            self.assertEqual(nabu_limine.stage_image_boot(source,output,'debug-next'),release)
            self.assertFalse((output/'efi').exists())
            self.assertFalse((output/'grub2').exists())
            self.assertFalse((output/'EFI/arch').exists())
            self.assertEqual((output/'EFI/Linux/omarchy.efi').read_bytes(),uki.read_bytes())
            self.assertEqual((output/('vmlinux-'+release)).read_bytes(),b'kernel')
            self.assertEqual((output/('dtb-'+release)).read_bytes(),b'dtb')
            self.assertEqual((source/'boot/grub2/grub.cfg').read_text(),'historical')
            dtb.write_bytes(b'wrong')
            with self.assertRaisesRegex(ValueError,'kernel/DTB'):
                nabu_limine.stage_image_boot(source,root/'bad','debug-next')
            self.assertFalse((root/'bad').exists())


class RuntimeConfig(unittest.TestCase):
    def test_board_commandline_is_complete_and_snapshot_defaults_are_preserved(self):
        cmdline='root=PARTLABEL=userdata rootfstype=btrfs rootflags=subvol=@ rw\nacpi=off fbcon=rotate:1\n'
        config=nabu_limine.runtime_config('local',cmdline)
        self.assertIn('KERNEL_CMDLINE[default]="'+' '.join(cmdline.split())+'"',config)
        self.assertIn('ESP_PATH="/boot"',config)
        self.assertIn('RESTORE_METHOD=replace',config)
        self.assertIn('MAX_SNAPSHOT_ENTRIES=6',config)
        self.assertIn('LIMIT_USAGE_PERCENT=85',config)
        self.assertIn('--ukiconfig /etc/kernel/uki-nabu-local.conf',config)
        self.assertNotIn('machine-id',config)
        self.assertNotIn('linux-t2',config)
        self.assertIn('FIND_BOOTLOADERS=no',config)

    def test_wrong_root_and_config_injection_are_rejected(self):
        base='root=PARTLABEL=userdata rootfstype=btrfs rootflags=subvol=@ rw'
        for text in (base+' root=/dev/wrong',base.replace('btrfs','ext4'),
                     base.replace('subvol=@','subvolid=5'),base+' keep_bootcon',
                     base+' $(command)',base+' "\\nFIND_BOOTLOADERS=yes'):
            with self.subTest(text=text),self.assertRaises(ValueError):
                nabu_limine.runtime_config('local',text)
