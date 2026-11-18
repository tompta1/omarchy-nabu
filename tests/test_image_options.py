import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import nabu_image_options as options
import nabu_desktop as desktop
import nabu_screensaver as screensaver
import importlib.machinery
import importlib.util

loader = importlib.machinery.SourceFileLoader('option_compat', str(ROOT/'scripts/omarchy-nabu-shell-compat'))
spec = importlib.util.spec_from_loader(loader.name, loader)
compat = importlib.util.module_from_spec(spec)
loader.exec_module(compat)


class ImageOptions(unittest.TestCase):
    def test_only_selected_optional_logo_argument_changes_the_common_cmdline(self):
        base = (ROOT/'profiles/nabu/kernel.cmdline').read_text()
        self.assertEqual(options.kernel_cmdline(base, options.DEFAULT), base)
        selected = {**options.DEFAULT, 'hide_kernel_logos': True}
        self.assertEqual(options.kernel_cmdline(base, selected).split(), base.split()+['logo.nologo'])
        with self.assertRaisesRegex(ValueError, 'optional logo preference'):
            options.kernel_cmdline(base+' logo.nologo', options.DEFAULT)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root/'.omarchy-nabu-build-root').touch()
        for relative, text in ((compat.RELATIVE, compat.ORIGINAL),
                               (compat.MEDIA_RELATIVE, compat.MEDIA_ORIGINAL),
                               (screensaver.FOOT_RELATIVE, screensaver.FOOT_ORIGINAL)):
            path = self.root/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.selected = {**options.DEFAULT, 'display_controls': True,
                        'screensaver_touch_dismiss': True, 'hide_kernel_logos': True}

    def configure(self, selected):
        desktop.configure(self.root, selected)
        return subprocess.run([sys.executable, str(self.root/'usr/local/libexec/omarchy-nabu-shell-compat'),
                               '--root', str(self.root)], capture_output=True, text=True)

    def test_default_options_default_reuse_and_repeat_upgrades_converge(self):
        for selected in (options.DEFAULT, self.selected, self.selected, options.DEFAULT, options.DEFAULT):
            with self.subTest(selected=selected):
                result = self.configure(selected)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(options.installed_options(self.root), selected)
                self.assertEqual((self.root/screensaver.FOOT_RELATIVE).read_text(),
                                 screensaver.FOOT_REPLACEMENT if selected['screensaver_touch_dismiss'] else screensaver.FOOT_ORIGINAL)
                self.assertEqual((self.root/desktop.SETUP_LINK).is_symlink(), selected['display_controls'])
                for relative, source in desktop.OPTIONAL_FILES.items():
                    if selected['display_controls']:
                        self.assertEqual((self.root/relative).read_bytes(), (ROOT/source).read_bytes())
                    else:
                        self.assertFalse((self.root/relative).exists())
                for relative, source in desktop.SCREENSAVER_FILES.items():
                    if selected['screensaver_touch_dismiss']:
                        self.assertEqual((self.root/relative).read_bytes(), (ROOT/source).read_bytes())
                    else:
                        self.assertFalse((self.root/relative).exists())
                self.assertIn(compat.REPLACEMENT, (self.root/compat.RELATIVE).read_text())
                self.assertIn(compat.MEDIA_REPLACEMENT, (self.root/compat.MEDIA_RELATIVE).read_text())

    def test_mixed_selection_does_not_enable_other_preferences(self):
        selected = {**options.DEFAULT, 'screensaver_touch_dismiss': True}
        self.assertEqual(self.configure(selected).returncode, 0)
        self.assertEqual((self.root/screensaver.FOOT_RELATIVE).read_text(), screensaver.FOOT_REPLACEMENT)
        self.assertFalse((self.root/desktop.SETUP_LINK).exists())
        self.assertFalse(options.installed_options(self.root)['hide_kernel_logos'])

    def test_upgrade_hook_failure_preserves_unknown_screensaver(self):
        self.assertEqual(self.configure(self.selected).returncode, 0)
        target = self.root/screensaver.FOOT_RELATIVE
        target.write_text('new upstream or user preference\n')
        result = subprocess.run([sys.executable, str(self.root/'usr/local/libexec/omarchy-nabu-screensaver-touch'),
                                 '--root', str(self.root)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('preserving it for review', result.stderr)
        self.assertEqual(target.read_text(), 'new upstream or user preference\n')

    def test_unknown_optional_file_is_preserved_before_any_configuration(self):
        target = self.root/'usr/local/bin/omarchy-nabu-display-control'
        target.parent.mkdir(parents=True)
        target.write_text('user edit')
        with self.assertRaisesRegex(ValueError, 'preserving it for review'):
            desktop.configure(self.root, options.DEFAULT)
        self.assertEqual(target.read_text(), 'user edit')
        self.assertFalse((self.root/options.INSTALLED).exists())

    def test_user_clone_and_external_symlink_refuse_default_reuse(self):
        clone = self.root/'home/tablet/.config/omarchy/plugins/nabu.display'
        clone.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'user display clone'):
            desktop.configure(self.root, options.DEFAULT)
        clone.rmdir()
        path = self.root/'usr/local/bin/omarchy-nabu-display-control'
        path.parent.mkdir(parents=True)
        path.symlink_to('/unrelated/file')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            desktop.configure(self.root, options.DEFAULT)

    def test_requires_a_marked_root(self):
        (self.root/'.omarchy-nabu-build-root').unlink()
        with self.assertRaisesRegex(ValueError, 'marked'):
            desktop.configure(self.root, self.selected)

    def test_boolean_types_unknown_options_and_schema_are_rejected(self):
        for value in ({**options.DEFAULT, 'display_controls': 'false'},
                      {**options.DEFAULT, 'display_controls': 1},
                      {**options.DEFAULT, 'schema': True},
                      {**options.DEFAULT, 'unexpected': False}, {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                options.validate(value)
        self.assertEqual(options.load_options(ROOT/'profiles/nabu/image-options.default.json'), options.DEFAULT)
        self.assertEqual(options.digest(dict(reversed(list(self.selected.items())))), options.digest(self.selected))


if __name__ == '__main__':
    unittest.main()
