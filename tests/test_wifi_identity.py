import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('wifi_identity', str(ROOT/'scripts/omarchy-nabu-wifi-identity'))
spec = importlib.util.spec_from_loader(loader.name, loader)
wifi = importlib.util.module_from_spec(spec)
loader.exec_module(wifi)


class WifiIdentity(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, name, text):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_empty_dmi_falls_back_to_nul_terminated_device_tree(self):
        self.write(wifi.SOURCES[0], '')
        self.write(wifi.SOURCES[1], 'test-serial\0')
        self.assertEqual(wifi.identity(self.root), (wifi.SOURCES[1], 'test-serial'))

    def test_existing_dmi_identity_keeps_upstream_mac_algorithm(self):
        self.write(wifi.SOURCES[0], 'test-serial\n')
        self.write(wifi.SOURCES[1], 'other-serial\0')
        source, value = wifi.identity(self.root)
        self.assertEqual(source, wifi.SOURCES[0])
        self.assertEqual(wifi.stable_mac(value), 'ca:6f:11:46:ff:76')

    def test_mac_is_stable_local_unicast_and_device_specific(self):
        mac = wifi.stable_mac('one-device')
        self.assertEqual(mac, wifi.stable_mac('one-device'))
        self.assertEqual(int(mac.split(':')[0], 16) & 3, 2)
        self.assertNotEqual(mac, wifi.stable_mac('another-device'))

    def test_no_shared_fallback_for_missing_identity(self):
        self.write(wifi.SOURCES[0], 'unknown')
        self.write(wifi.SOURCES[1], '00000000')
        with self.assertRaises(RuntimeError):
            wifi.identity(self.root)

    def test_configure_is_idempotent_and_sets_only_nm_default(self):
        mac = wifi.stable_mac('test-serial')
        wifi.configure(self.root, mac)
        file = self.root/'etc/NetworkManager/conf.d/90-nabu-wifi-identity.conf'
        first = file.stat().st_mtime_ns
        wifi.configure(self.root, mac)
        self.assertEqual(file.stat().st_mtime_ns, first)
        self.assertIn('match-device=type:wifi', file.read_text())
        self.assertIn('wifi.cloned-mac-address='+mac, file.read_text())


if __name__ == '__main__':
    unittest.main()
