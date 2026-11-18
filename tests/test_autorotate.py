import contextlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


loader = importlib.machinery.SourceFileLoader(
    'nabu_autorotate', str(Path(__file__).resolve().parents[1]/'scripts/omarchy-nabu-autorotate'))
spec = importlib.util.spec_from_loader(loader.name, loader)
rotation = importlib.util.module_from_spec(spec)
loader.exec_module(rotation)


class AutoRotate(unittest.TestCase):
    def apply(self, current=0, dpms=True):
        calls = []
        monitor = {'name': 'DSI-1', 'width': 1600, 'height': 2560,
                   'refreshRate': 119.9987, 'scale': 1.75, 'x': 23, 'y': 40,
                   'transform': current, 'dpmsStatus': dpms}

        def hypr(*args):
            calls.append(args)
            return json.dumps([monitor]) if args[0] == 'monitors' else 'ok'

        with patch.object(rotation, 'hypr', side_effect=hypr), \
             patch.object(rotation, 'rotation_enabled', return_value=True), \
             patch.object(rotation, 'pending', 'right-up'), \
             contextlib.redirect_stdout(io.StringIO()):
            rotation.rotate()
        return calls

    def test_rotation_only_changes_angle_without_reconstructing_mode_or_scale(self):
        expression = self.apply()[-1][1]
        self.assertIn('hl.monitor({ output = "DSI-1", transform = 3 })', expression)
        self.assertIn('touchdevice = { output = "DSI-1", transform = 3', expression)
        for field in ('mode =', 'scale =', 'position =', 'refresh'):
            self.assertNotIn(field, expression)

    def test_already_rotated_display_still_synchronizes_touch(self):
        expression = self.apply(current=3)[-1][1]
        self.assertNotIn('hl.monitor', expression)
        self.assertIn('touchdevice', expression)

    def test_sleeping_display_is_not_reconfigured(self):
        self.assertEqual(len(self.apply(dpms=False)), 1)

    def test_undefined_sensor_reading_cancels_pending_turn(self):
        sensor = Mock()
        sensor.get_cached_property.return_value.unpack.return_value = 'undefined'
        glib = Mock()
        with patch.object(rotation, 'proxy', sensor), \
             patch.object(rotation, 'GLib', glib, create=True), \
             patch.object(rotation, 'claimed', True), \
             patch.object(rotation, 'pending', 'left-up'), \
             patch.object(rotation, 'timer', 123), \
             patch.object(rotation, 'rotation_enabled', return_value=True):
            self.assertTrue(rotation.tick())
            glib.source_remove.assert_called_once_with(123)
            self.assertIsNone(rotation.pending)
            self.assertEqual(rotation.timer, 0)


if __name__ == '__main__':
    unittest.main()
