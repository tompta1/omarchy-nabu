from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/apply-root-overlay.sh'


class RootOverlay(unittest.TestCase):
    def test_private_checkout_preserves_system_directory_modes(self):
        with tempfile.TemporaryDirectory() as temp:
            overlay = Path(temp) / 'overlay'
            root = Path(temp) / 'root'
            for directory, mode in (
                (overlay, 0o700), (overlay / 'usr', 0o700),
                (overlay / 'usr/libexec', 0o700), (root, 0o755),
                (root / 'usr', 0o755), (overlay / 'private', 0o700),
                (root / 'private', 0o750),
            ):
                directory.mkdir()
                directory.chmod(mode)

            executable = overlay / 'usr/libexec/helper'
            executable.write_text('#!/bin/sh\n')
            executable.chmod(0o700)
            config = overlay / 'usr/config'
            config.write_text('enabled\n')
            config.chmod(0o600)

            subprocess.run(['bash', SCRIPT, overlay, root], check=True)

            for path, mode in (
                (root, 0o755), (root / 'usr', 0o755),
                (root / 'private', 0o750), (root / 'usr/libexec', 0o755),
                (root / 'usr/libexec/helper', 0o755),
                (root / 'usr/config', 0o644),
            ):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode, path)
            self.assertEqual((root / 'usr/config').read_text(), 'enabled\n')
