import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('private_state',ROOT/'scripts/personalize-tablet-images.py')
module=importlib.util.module_from_spec(spec)
import sys
sys.path.insert(0,str(ROOT/'scripts'))
spec.loader.exec_module(module)


class PrivateStateTest(unittest.TestCase):
    def test_scope_does_not_import_host_identity_or_user_files(self):
        self.assertTrue(module.selected('etc/NetworkManager/system-connections/home.nmconnection'))
        self.assertTrue(module.selected('var/lib/bluetooth/AA:BB:CC:DD:EE:FF/settings'))
        for path in ('etc/ssh/ssh_host_ed25519_key','etc/machine-id','home/tablet/.ssh/authorized_keys','mnt/vendor/persist/calibration'):
            self.assertFalse(module.selected(path))
        for path in ('/etc/passwd','var/lib/bluetooth/../../etc/passwd'):
            with self.assertRaises(ValueError):module.selected(path)

    def test_debugfs_arguments_cannot_inject_commands(self):
        self.assertEqual(module.quote('a "quoted" name'),'"a \\"quoted\\" name"'.replace('\\\\','\\'))
        with self.assertRaises(ValueError):module.quote('profile\nrm /etc/passwd')


if __name__=='__main__':unittest.main()
