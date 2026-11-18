from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from nabu_grub import menu_config


class GrubMenu(unittest.TestCase):
    def setUp(self):
        self.layout={'boot_filesystem_uuid':'00000000-0000-4000-8000-000000000001',
                     'esp_filesystem_uuid':'ABCD-1234'}
        self.profile={'recovery_loader':'/EFI/fedora/grubaa64-2.12-recovery.efi',
                      'recovery_version':'2.12'}

    def test_recovery_is_conditional_and_keeps_both_kernel_entries(self):
        menu=menu_config(self.layout,self.profile)
        self.assertTrue(menu.startswith('set timeout=10\nset default=0\n'))
        self.assertIn('chainloader /EFI/arch/arch-linux-nabu-debug.efi',menu)
        self.assertIn('chainloader /EFI/arch/arch-linux-nabu-stock.efi',menu)
        self.assertIn('if [ -f ($nabu_esp)'+self.profile['recovery_loader']+' ]; then',menu)
        self.assertIn('--set=root ABCD-1234',menu)
        self.assertNotIn('recovery',menu_config(self.layout,{}))

    def test_invalid_recovery_mapping_is_rejected(self):
        for name,value in [('recovery_loader','/EFI/../outside.efi'),
                           ('recovery_loader','/EFI/test.efi; reboot'),
                           ('recovery_version',"2.12' { reboot; }")]:
            with self.subTest(name=name,value=value), self.assertRaises(ValueError):
                menu_config(self.layout,{**self.profile,name:value})
        with self.assertRaises(ValueError):
            menu_config({**self.layout,'boot_filesystem_uuid':'invalid'},self.profile)

    def test_normal_slot_keeps_stock_and_uses_plain_label(self):
        menu = menu_config(self.layout, self.profile, 'local')
        self.assertIn("menuentry 'Omarchy Nabu'", menu)
        self.assertIn('chainloader /EFI/arch/arch-linux-nabu-local.efi', menu)
        self.assertIn('chainloader /EFI/arch/arch-linux-nabu-stock.efi', menu)
        self.assertNotIn('arch-linux-nabu-debug.efi', menu)
        with self.assertRaises(ValueError):
            menu_config(self.layout, self.profile, '../unreviewed')


if __name__=='__main__': unittest.main()
