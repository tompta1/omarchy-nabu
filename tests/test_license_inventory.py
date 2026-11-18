import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('license_inventory', ROOT/'scripts/audit-package-licenses.py')
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


class LicenseInventory(unittest.TestCase):
    def test_declared_license_is_not_an_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root/'var/lib/pacman/local/example-1-1'
            package.mkdir(parents=True)
            (package/'desc').write_text('%NAME%\nexample\n\n%VERSION%\n1-1\n\n%LICENSE%\nGPL-2.0-only\n')
            (package/'files').write_text('%FILES%\nusr/share/licenses/example/LICENSE\n')
            notice = root/'usr/share/licenses/example/LICENSE'
            notice.parent.mkdir(parents=True)
            notice.write_text('license fixture')
            report = inventory.audit(root)
            self.assertFalse(report['redistribution_approved'])
            self.assertEqual(report['summary']['copyleft_source_reviews'], 1)
            self.assertEqual(len(report['packages'][0]['notice_files']), 1)
            (package/'desc').write_text('%NAME%\nexample\n\n%VERSION%\n1-1\n\n%LICENSE%\ncustom:proprietary\n')
            report = inventory.audit(root)
            self.assertEqual(report['summary']['individual_license_reviews'], 1)
            self.assertFalse(report['redistribution_approved'])

    def test_external_metadata_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base/'root'
            package = root/'var/lib/pacman/local/example-1-1'
            package.mkdir(parents=True)
            outside = base/'outside'
            outside.write_text('%NAME%\nexample\n%VERSION%\n1-1\n')
            (package/'desc').symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'leaves supplied root'):
                inventory.audit(root)


if __name__ == '__main__':
    unittest.main()
