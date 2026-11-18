import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT/'scripts'/name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    result = importlib.util.module_from_spec(spec)
    loader.exec_module(result)
    return result


control = module('omarchy-nabu-display-control')
installer = module('omarchy-nabu-display-install')


class DisplayControls(unittest.TestCase):
    def test_first_login_retries_existing_clone_without_replacing_user_qml(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = home/'.config/omarchy/plugins/nabu.display'
            target.mkdir(parents=True)
            (target/'nabu-display-receipt.json').write_text('{}')
            (target/'OMARCHY-LICENSE').write_text('notice fixture')
            (target/'NabuDisplay.qml').write_text('preserved user edits')
            marker = home/'.local/state/omarchy-nabu/display-setup.done'
            with patch.object(installer.Path, 'home', return_value=home), \
                 patch.object(installer.os, 'geteuid', return_value=1000), \
                 patch.object(installer, 'wait_for_shell'), \
                 patch.object(sys, 'argv', ['installer', '--session-setup']):
                with patch.object(installer, 'enable_clone', side_effect=ValueError('IPC unavailable')):
                    with self.assertRaisesRegex(ValueError, 'IPC unavailable'):
                        installer.main()
                self.assertFalse(marker.exists())
                self.assertEqual((target/'NabuDisplay.qml').read_text(), 'preserved user edits')
                with patch.object(installer, 'enable_clone') as enable:
                    installer.main()
                    enable.assert_called_once_with('nabu.display')
                self.assertTrue(marker.exists())
                with patch.object(installer, 'enable_clone') as enable:
                    installer.main()
                    enable.assert_not_called()

    def test_clone_requires_and_preserves_the_full_license_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upstream = root/'upstream'; upstream.mkdir()
            target = root/'clone'; target.mkdir()
            with self.assertRaisesRegex(ValueError, 'notice is missing'):
                installer.copy_license(upstream, target, [])
            self.assertFalse((target/'OMARCHY-LICENSE').exists())
            notice = root/'packaged-license'
            notice.write_text('copyright and permission fixture\n')
            installer.copy_license(upstream, target, [notice])
            self.assertEqual((target/'OMARCHY-LICENSE').read_bytes(), notice.read_bytes())

    def test_unsupported_widget_is_rejected_without_mutation(self):
        with self.assertRaises(ValueError):
            installer.render_panel('unrelated or changed upstream widget')

    def test_integration_keeps_slider_and_adds_only_one_control_component(self):
        original = ('Panel {\n  command: ["omarchy-brightness-display", "--monitor", "DSI-1"]\n'
                    '          // ---------- Text size ----------\n  PanelSlider {}\n}\n')
        output = installer.render_panel(original)
        self.assertEqual(output.count('NabuControls {'), 1)
        self.assertIn('PanelSlider {}', output)
        self.assertIn('"manual-brightness"', output)
        self.assertEqual(original.count('NabuControls'), 0)

    def test_rotation_preference_is_persisted_without_disabling_service(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory)/'controls.json'
            with patch.object(control, 'CONFIG', file), patch.object(control, 'status', return_value={}):
                with patch.object(sys, 'argv', ['control', 'toggle-rotation']):
                    control.main()
                self.assertFalse(json.loads(file.read_text())['auto_rotate'])
                with patch.object(sys, 'argv', ['control', 'toggle-rotation']):
                    control.main()
                self.assertTrue(json.loads(file.read_text())['auto_rotate'])

    def test_manual_slider_disables_auto_before_delegating(self):
        events = []
        with patch.object(sys, 'argv', ['control', 'manual-brightness', '--no-osd', '--monitor', 'DSI-1', '37%']), \
             patch.object(control, 'brightness', side_effect=lambda action: events.append(action)), \
             patch.object(control.subprocess, 'run', side_effect=lambda *args, **kwargs: events.append(args[0])), \
             patch.object(control, 'status', return_value={}):
            control.main()
        self.assertEqual(events[0], 'disable')
        self.assertEqual(events[1], ['omarchy-brightness-display', '--no-osd', '--monitor', 'DSI-1', '37%'])


if __name__ == '__main__':
    unittest.main()
