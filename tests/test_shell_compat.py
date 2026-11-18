import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))


loader = importlib.machinery.SourceFileLoader(
    'shell_compat', str(Path(__file__).resolve().parents[1]/'scripts/omarchy-nabu-shell-compat'))
spec = importlib.util.spec_from_loader(loader.name, loader)
compat = importlib.util.module_from_spec(spec)
loader.exec_module(compat)
import nabu_screensaver as screensaver


class ShellCompat(unittest.TestCase):
    def test_screensaver_update_preserves_backup_and_regular_terminal_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/screensaver.FOOT_RELATIVE
            target.parent.mkdir(parents=True)
            target.write_text(screensaver.FOOT_ORIGINAL)
            regular = root/'home/tablet/.config/foot/foot.ini'
            regular.parent.mkdir(parents=True)
            regular.write_text('user terminal preferences')
            screensaver.apply_screensaver(root)
            first = target.read_bytes()
            screensaver.apply_screensaver(root)
            self.assertEqual(target.read_bytes(), first)
            self.assertEqual(regular.read_text(), 'user terminal preferences')
            backup = root/'var/lib/omarchy-nabu/compat/screensaver-foot.ini.original'
            self.assertEqual(backup.read_text(), screensaver.FOOT_ORIGINAL)

    def test_custom_screensaver_config_is_preserved_without_backup_side_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/screensaver.FOOT_RELATIVE
            target.parent.mkdir(parents=True)
            target.write_text(screensaver.FOOT_ORIGINAL+'\n[mouse-bindings]\nquit=BTN_MIDDLE\n')
            with self.assertRaises(ValueError): screensaver.apply_screensaver(root)
            self.assertIn('quit=BTN_MIDDLE',target.read_text())
            self.assertFalse((root/'var/lib/omarchy-nabu').exists())

    def test_wallpaper_backup_survives_reapplication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/compat.MEDIA_RELATIVE
            target.parent.mkdir(parents=True)
            target.write_text(compat.MEDIA_ORIGINAL)
            compat.apply_media(root)
            compat.apply_media(root)
            self.assertIn('retainWhileLoading: true', target.read_text())
            backup = root/'var/lib/omarchy-nabu/compat/BackgroundMedia.qml.original'
            self.assertEqual(backup.read_text(), compat.MEDIA_ORIGINAL)

    def test_changed_wallpaper_implementation_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/compat.MEDIA_RELATIVE
            target.parent.mkdir(parents=True)
            target.write_text('new upstream wallpaper')
            with self.assertRaises(ValueError):
                compat.apply_media(root)
            self.assertEqual(target.read_text(), 'new upstream wallpaper')

    def test_unknown_upstream_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/compat.RELATIVE
            target.parent.mkdir(parents=True)
            target.write_text('new upstream implementation')
            with self.assertRaises(ValueError):
                compat.apply(root)
            self.assertEqual(target.read_text(), 'new upstream implementation')
            self.assertFalse((root/'var/lib/omarchy-nabu').exists())

    def test_repeated_install_preserves_original_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/compat.RELATIVE
            target.parent.mkdir(parents=True)
            target.write_text(compat.ORIGINAL)
            compat.apply(root)
            compat.apply(root)
            self.assertEqual(target.read_text(), compat.REPLACEMENT)
            backup = root/'var/lib/omarchy-nabu/compat/ScreenMoveRemap.qml.original'
            self.assertEqual(backup.read_text(), compat.ORIGINAL)


if __name__ == '__main__':
    unittest.main()
