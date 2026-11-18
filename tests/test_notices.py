import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import nabu_notices


class SourceNotices(unittest.TestCase):
    def test_install_requires_a_build_root(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'marked'):
                nabu_notices.install(Path(directory))

    def test_exact_notices_do_not_approve_firmware_or_touch_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'.omarchy-nabu-build-root').touch()
            firmware = root/'usr/lib/firmware/example.bin'
            firmware.parent.mkdir(parents=True)
            firmware.write_bytes(b'unrelated firmware fixture')
            nabu_notices.install(root)
            result = json.loads((root/nabu_notices.MANIFEST).read_text())
            self.assertEqual(result, nabu_notices.manifest())
            self.assertFalse(result['whole_image_redistribution_approved'])
            for name, source in nabu_notices.NOTICES.items():
                self.assertEqual((root/nabu_notices.DESTINATION/name).read_bytes(), (ROOT/source).read_bytes())
            self.assertEqual(firmware.read_bytes(), b'unrelated firmware fixture')
            nabu_notices.install(root)
            self.assertEqual(json.loads((root/nabu_notices.MANIFEST).read_text()), result)
