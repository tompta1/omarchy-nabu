import hashlib
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DeviceLayerTest(unittest.TestCase):
    def test_references_and_local_integrations_exist(self):
        manifest = json.loads((ROOT/'profiles/nabu/device-layer.json').read_text())
        sources = json.loads((ROOT/'sources.json').read_text())
        self.assertTrue(set(manifest['reference_sources']) <= sources.keys())
        for component in manifest['components']:
            for key in ('selected_recipe', 'integration', 'selected_integration', 'selected_integrations'):
                values = component.get(key, [])
                if isinstance(values, str): values = [values]
                for value in values:
                    self.assertTrue((ROOT/value).is_file(), value)

    def test_local_patch_hashes_are_pinned_in_their_recipes(self):
        for name in ('libssc', 'iio-sensor-proxy'):
            directory = ROOT/'packages'/name
            recipe = (directory/'PKGBUILD').read_text()
            self.assertNotIn('SKIP', recipe)
            for patch in directory.glob('*.patch'):
                self.assertIn(hashlib.sha256(patch.read_bytes()).hexdigest(), recipe, str(patch))

    def test_device_overlay_cannot_import_legacy_boot_or_binary_payloads(self):
        paths = [line for line in (ROOT/'profiles/nabu/kumar-overlay.files').read_text().splitlines()
                 if line and not line.startswith('#')]
        self.assertEqual(len(paths), len(set(paths)))
        for path in paths:
            self.assertFalse(Path(path).is_absolute())
            self.assertNotIn('..', Path(path).parts)
            self.assertTrue(path.startswith(('etc/', 'usr/')))
            self.assertNotRegex(path, r'\.(efi|img|tgz|bin|mbn)$')
            self.assertNotIn('flash', path)

    def test_release_and_repository_policy_agree(self):
        manifest = json.loads((ROOT/'profiles/nabu/device-layer.json').read_text())
        policy = json.loads((ROOT/'profiles/nabu/repositories.json').read_text())
        self.assertEqual(manifest['desktop']['channel'], policy['channel'])
        self.assertEqual(manifest['desktop']['release'], 'v'+policy['desktop_version'])

    def test_debug_preset_keeps_nabu_initramfs_dropins(self):
        recipe = (ROOT/'kernel/PKGBUILD').read_text()
        # Passing an explicit -c config suppresses mkinitcpio.conf.d, including
        # the Nabu override which removes builder-host autodetection.
        self.assertNotIn('ALL_config=', recipe)
        self.assertNotIn('--config /etc/mkinitcpio.conf', recipe)
        self.assertTrue((ROOT/'overlay/etc/vconsole.conf').is_file())


if __name__ == '__main__':
    unittest.main()
