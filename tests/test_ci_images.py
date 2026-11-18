import io
from pathlib import Path
import runpy
import subprocess
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
remove_work_volumes = runpy.run_path(str(ROOT / 'scripts/ci-images.py'))['remove_work_volumes']


class ImageCleanup(unittest.TestCase):
    def test_backend_error_is_not_treated_as_absent_volume(self):
        replies = [
            subprocess.CompletedProcess([], 0, stdout=''),
            subprocess.CompletedProcess([], 125),
            subprocess.CompletedProcess([], 0, stdout=''),
            subprocess.CompletedProcess([], 1),
        ]
        log = io.StringIO()
        with patch('subprocess.run', side_effect=replies) as run:
            self.assertFalse(remove_work_volumes(['podman'], ['unknown', 'absent'], log))
        self.assertIn('Could not verify whether unknown exists', log.getvalue())
        self.assertEqual(run.call_args_list[-1].args[0], ['podman', 'volume', 'exists', 'absent'])

    def test_run_owned_container_is_removed_before_its_volume(self):
        replies = [
            subprocess.CompletedProcess([], 0, stdout='test-container\n'),
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 0),
        ]
        with patch('subprocess.run', side_effect=replies) as run:
            self.assertTrue(remove_work_volumes(['podman'], ['test-volume'], io.StringIO()))
        self.assertEqual([call.args[0] for call in run.call_args_list[1:]], [
            ['podman', 'rm', '--force', 'test-container'],
            ['podman', 'volume', 'exists', 'test-volume'],
            ['podman', 'volume', 'rm', 'test-volume'],
        ])
