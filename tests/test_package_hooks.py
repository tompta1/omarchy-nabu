from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from nabu_package_hooks import prepare


class BuildHooks(unittest.TestCase):
    def test_incoming_hooks_are_masked_before_they_exist_on_disk(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'.omarchy-nabu-build-root').touch()
            installed = root/'etc/pacman.d/hooks'; installed.mkdir(parents=True)
            (installed/'90-linux-nabu.hook').write_text('old hook')
            output = root/'private-hooks'; output.mkdir()
            incoming = ('usr/share/libalpm/hooks/80-limine-efi-deploy.hook\n'
                        './etc/pacman.d/hooks/90-mkinitcpio-install.hook\n'
                        'usr/share/libalpm/hooks/30-systemd-daemon-reload-system.hook\n')
            with patch('nabu_package_hooks.subprocess.check_output',return_value=incoming):
                names = prepare(root,output,[root/'new.pkg.tar.zst'])
            self.assertEqual(set(names),{'90-linux-nabu.hook','80-limine-efi-deploy.hook',
                                         '90-mkinitcpio-install.hook'})
            self.assertTrue(all((output/name).readlink()==Path('/dev/null') for name in names))
            self.assertEqual((installed/'90-linux-nabu.hook').read_text(),'old hook')
            with self.assertRaisesRegex(ValueError,'empty private'):
                prepare(root,output,[])

    def test_unmarked_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); output=root/'hooks'; output.mkdir()
            with self.assertRaisesRegex(ValueError,'marked isolated'):
                prepare(root,output,[])
