from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from nabu_grub import candidate_menu, limine_candidate_menu


class GrubCandidate(unittest.TestCase):
    def test_request_is_cleared_and_saved_before_selection(self):
        original = "set default=0\nmenuentry 'Working' { echo working; }\n"
        text = candidate_menu(original, '11111111-2222-4333-8444-555555555555')
        self.assertIn("menuentry 'Working' { echo working; }", text)
        self.assertLess(text.index('set nabu_once='), text.index('if save_env'))
        self.assertLess(text.index('if save_env'), text.index('set default=nabu-candidate'))
        self.assertEqual(text.count('set default=0\n'), 1)
        self.assertIn('arch-linux-nabu-debug-next.efi', text)
        with self.assertRaises(ValueError):
            candidate_menu(text, '11111111-2222-4333-8444-555555555555')

    def test_unknown_menu_and_invalid_identity_rejected(self):
        for current, identity in (('set default=1\n', '11111111-2222-4333-8444-555555555555'),
                                  ('set default=0\n', "bad'; reboot")):
            with self.assertRaises(ValueError):
                candidate_menu(current, identity)


class LimineMenu(unittest.TestCase):
    def test_independent_request_and_existing_kernel_entry_retained(self):
        identity = '11111111-2222-4333-8444-555555555555'
        current = candidate_menu("set default=0\nmenuentry 'Working' { echo ok; }\n", identity)
        result = limine_candidate_menu(current, identity, '1234-ABCD')
        self.assertIn("menuentry 'Working' { echo ok; }", result)
        self.assertIn("--id 'nabu-candidate'", result)
        self.assertLess(result.index('set limine_once='), result.index('if save_env --file /grub2/nabu-limine.env'))
        self.assertLess(result.index('if save_env --file /grub2/nabu-limine.env'), result.index('set default=nabu-limine'))
        self.assertIn('chainloader /EFI/omarchy-nabu/limine/BOOTAA64.EFI', result)
        with self.assertRaises(ValueError):
            limine_candidate_menu(result, identity, '1234-ABCD')
        with self.assertRaises(ValueError):
            limine_candidate_menu(current, identity, "1234-ABCD; reboot")
