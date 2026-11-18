import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
spec = importlib.util.spec_from_file_location('grub_test', ROOT/'scripts/prepare-grub-test.py')
grub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grub)


class GrubTestPreparation(unittest.TestCase):
    def setUp(self):
        self.original = ('set timeout=10\nset default=0\n'
                         "menuentry 'debug' {\n chainloader /EFI/arch/arch-linux-nabu-debug.efi\n}\n"
                         "menuentry 'stock' {\n chainloader /EFI/arch/arch-linux-nabu-stock.efi\n}\n")

    def test_selection_requires_successful_consumption_and_preserves_entries(self):
        config = grub.test_config(self.original, 'test-boot-uuid')
        guard = config.index('if save_env ')
        selection = config.index('set default="'+grub.ENTRY+'"')
        self.assertLess(config.index('set next_entry='), guard)
        self.assertLess(guard, selection)
        self.assertIn(self.original.split('set default=0\n', 1)[1], config)
        self.assertTrue(config.startswith('set timeout=10\nset default=0\n'))

    def test_unknown_menu_missing_fallback_and_double_stage_are_rejected(self):
        for config in (self.original.replace('set default=0', 'set default=2'),
                       self.original.replace('arch-linux-nabu-stock.efi', 'missing.efi'),
                       grub.test_config(self.original, 'test-boot-uuid')):
            with self.subTest(config=config), self.assertRaises(ValueError):
                grub.test_config(config, 'test-boot-uuid')

    def test_iris_candidate_is_one_shot_and_preserves_working_entries(self):
        for test, slot in (('iris-debug6', 'debug-next'),):
            with self.subTest(test=test):
                config = grub.test_config(self.original, 'test-boot-uuid', test)
                self.assertTrue(config.startswith('set timeout=10\nset default=0\n'))
                self.assertIn('if save_env ', config)
                self.assertIn('set default="nabu-'+test+'"', config)
                self.assertIn('chainloader /EFI/arch/arch-linux-nabu-'+slot+'.efi', config)
                self.assertIn(self.original.split('set default=0\n', 1)[1], config)
                for other in ('iris-debug3', 'iris-debug4', 'iris-debug5', 'iris-debug6'):
                    with self.assertRaises(ValueError):
                        grub.test_config(config, 'test-boot-uuid', other)

    def test_retired_candidates_cannot_be_selected_or_stacked(self):
        for test in ('iris-debug3', 'iris-debug4', 'iris-debug5'):
            with self.subTest(test=test):
                with self.assertRaisesRegex(ValueError, 'Retired'):
                    grub.test_config(self.original, 'test-boot-uuid', test)
                stale = self.original + '\n# old test: ' + grub.TESTS[test][0] + '\n'
                with self.assertRaisesRegex(ValueError, 'already staged'):
                    grub.test_config(stale, 'test-boot-uuid', 'iris-debug6')

    def test_environment_is_exactly_one_block_and_overflow_fails(self):
        block = grub.environment({'next_entry': grub.ENTRY})
        self.assertEqual(len(block), 1024)
        self.assertTrue(block.startswith(b'# GRUB Environment Block\nnext_entry='))
        with self.assertRaises(ValueError):
            grub.environment({'large': 'x'*1024})

    def test_stock_test_selects_existing_stock_uki_and_preserves_debug_default(self):
        config=grub.test_config(self.original,'test-boot-uuid','stock')
        self.assertIn('set default="nabu-stock-test"',config)
        self.assertIn("--id nabu-stock-test {",config)
        self.assertTrue(config.startswith('set timeout=10\nset default=0\n'))
        self.assertNotIn('grub-2.16-candidate.efi',config)
        self.assertEqual(config.count('chainloader /EFI/arch/arch-linux-nabu-stock.efi'),2)
        with self.assertRaises(ValueError): grub.test_config(config,'test-boot-uuid','stock')
        with self.assertRaises(ValueError): grub.test_config(self.original,'test-boot-uuid','arbitrary')

    def test_normal_kernel_uses_its_own_uki_without_replacing_working_entries(self):
        config = grub.test_config(self.original, 'test-boot-uuid', 'kernel-normal')
        self.assertTrue(config.startswith('set timeout=10\nset default=0\n'))
        self.assertIn(self.original.split('set default=0\n', 1)[1], config)
        self.assertIn('chainloader /EFI/arch/arch-linux-nabu-local.efi', config)
        self.assertLess(config.index('if save_env '), config.index('set default="nabu-normal-test"'))
        for test in ('kernel-normal', 'stock', 'iris-debug6'):
            with self.subTest(test=test), self.assertRaises(ValueError):
                grub.test_config(config, 'test-boot-uuid', test)


if __name__ == '__main__':
    unittest.main()
