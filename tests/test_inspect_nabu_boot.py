import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from test_inspect_nabu_uki import fixture

spec = importlib.util.spec_from_file_location('inspect_boot', Path(__file__).resolve().parents[1] / 'scripts/inspect-nabu-boot.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BootInspectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.boot = self.base / 'boot'
        (self.boot / 'grub2').mkdir(parents=True)
        (self.boot / 'EFI/arch').mkdir(parents=True)
        self.menu = self.boot / 'grub2/grub.cfg'
        entries = []
        for label, name in module.ENTRIES.items():
            (self.boot / 'EFI/arch' / name).write_bytes(fixture())
            entries.append(f"menuentry '{label}' {{\n  search --no-floppy --fs-uuid --set=root 11111111-2222-3333-4444-555555555555\n  chainloader /EFI/arch/{name}\n}}\n")
        self.menu.write_text(''.join(entries))
        modules = self.base / 'usr/lib/modules/7.3.4-nabu'
        (modules / 'kernel').mkdir(parents=True)
        (modules / 'kernel/example.ko').write_bytes(b'module')
        (modules / 'kernel/dep.ko').write_bytes(b'dependency')
        (modules / 'modules.dep').write_text('kernel/example.ko: kernel/dep.ko\nkernel/dep.ko:\n')

    def test_reviewed_entries(self):
        module.inspect(self.boot, self.base, '11111111-2222-3333-4444-555555555555')

    def test_normal_primary_requires_explicit_matching_targets(self):
        text = self.menu.read_text().replace('Omarchy Nabu debug', 'Omarchy Nabu').replace('arch-linux-nabu-debug.efi', 'arch-linux-nabu-local.efi')
        entries = module.parse_menu(text, '11111111-2222-3333-4444-555555555555', 'local')
        self.assertEqual(entries['Omarchy Nabu'], 'arch-linux-nabu-local.efi')
        with self.assertRaises(ValueError):
            module.parse_menu(text, '11111111-2222-3333-4444-555555555555')

    def test_changed_stock_target_and_missing_modules(self):
        self.menu.write_text(self.menu.read_text().replace('chainloader /EFI/arch/arch-linux-nabu-stock.efi', 'chainloader /EFI/arch/unrelated.efi'))
        with self.assertRaises(ValueError):
            module.inspect(self.boot, self.base, '11111111-2222-3333-4444-555555555555')
        self.setUp_new_menu()
        (self.base / 'usr/lib/modules/7.3.4-nabu/modules.dep').unlink()
        with self.assertRaises(ValueError):
            module.inspect(self.boot, self.base, '11111111-2222-3333-4444-555555555555')

    def test_rejects_missing_dependency_and_unsafe_path(self):
        modules = self.base / 'usr/lib/modules/7.3.4-nabu'
        (modules / 'kernel/dep.ko').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing module file'):
            module.inspect(self.boot, self.base, '11111111-2222-3333-4444-555555555555')
        (modules / 'modules.dep').write_text('kernel/example.ko: ../outside.ko\n')
        with self.assertRaisesRegex(ValueError, 'Unsafe module'):
            module.inspect(self.boot, self.base, '11111111-2222-3333-4444-555555555555')

    def test_rejects_wrong_boot_filesystem(self):
        with self.assertRaises(ValueError):
            module.inspect(self.boot, self.base, 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    def setUp_new_menu(self):
        self.menu.write_text(self.menu.read_text().replace('chainloader /EFI/arch/unrelated.efi', 'chainloader /EFI/arch/arch-linux-nabu-stock.efi'))


if __name__ == '__main__':
    unittest.main()
