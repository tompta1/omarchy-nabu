import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import nabu_paths


class KernelDefaults(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.project = Path(self.directory.name)
        (self.project/'kernel').mkdir()
        (self.project/'scripts').mkdir()
        for name in ('nabu_paths.py', 'build-debug-kernel.sh'):
            shutil.copy2(ROOT/'scripts'/name, self.project/'scripts'/name)
        self.profile = {'build': {'output': 'out/selected-kernel', 'work_volume': 'selected-kernel-work'}}
        (self.project/'kernel/sources.json').write_text(json.dumps(self.profile))
        self.environment = {key: value for key, value in os.environ.items() if not key.startswith('NABU_KERNEL_')}

    def test_image_input_follows_selected_profile_and_explicit_override(self):
        alternate = {**self.profile, 'build': {'output': 'out/older', 'work_volume': 'older-work'}}
        (self.project/'kernel/older.json').write_text(json.dumps(alternate))
        with patch.object(nabu_paths, 'PROJECT', self.project), patch.dict(os.environ, self.environment, clear=True):
            self.assertEqual(nabu_paths.kernel_input(), self.project/'out/selected-kernel')
            with patch.dict(os.environ, {'NABU_KERNEL_PROFILE': 'kernel/older.json'}):
                self.assertEqual(nabu_paths.kernel_input(), self.project/'out/older')
                self.assertEqual(nabu_paths.kernel_work_volume(), 'older-work')
                with patch.dict(os.environ, {'NABU_KERNEL_INPUT_REL': 'out/explicit'}):
                    self.assertEqual(nabu_paths.kernel_input(), self.project/'out/explicit')

    def test_invalid_output_and_volume_are_rejected(self):
        with patch.object(nabu_paths, 'PROJECT', self.project), patch.dict(os.environ, self.environment, clear=True):
            for value in ('../outside', 'kernel', 'out'):
                with patch.dict(os.environ, {'NABU_KERNEL_OUTPUT': value}), self.assertRaises(ValueError):
                    nabu_paths.kernel_output()
            with patch.dict(os.environ, {'NABU_KERNEL_WORK_VOLUME': '../bad'}), self.assertRaises(ValueError):
                nabu_paths.kernel_work_volume()

    def test_plan_and_completed_output_do_not_start_a_builder(self):
        marker = self.project/'builder-was-started'
        tool = self.project/'scripts/host-podman.sh'
        tool.write_text('#!/bin/bash\ntouch "'+str(marker)+'"\nexit 99\n')
        tool.chmod(0o755)
        command = ['bash', str(self.project/'scripts/build-debug-kernel.sh')]
        plan = subprocess.run(command+['--plan'], env=self.environment, capture_output=True, text=True)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn(str(self.project/'out/selected-kernel'), plan.stdout)
        self.assertIn('Work volume: selected-kernel-work', plan.stdout)
        output = self.project/'out/selected-kernel'
        output.mkdir(parents=True)
        (output/'SHA256SUMS').write_text('existing completed build\n')
        result = subprocess.run(command, env=self.environment, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('completed kernel output already exists', result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual((output/'SHA256SUMS').read_text(), 'existing completed build\n')


if __name__ == '__main__':
    unittest.main()
