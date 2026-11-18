import importlib.machinery
import importlib.util
import errno
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('pacman_sandbox', str(ROOT/'scripts/omarchy-nabu-pacman-sandbox'))
spec = importlib.util.spec_from_loader(loader.name, loader)
sandbox = importlib.util.module_from_spec(spec)
loader.exec_module(sandbox)


class PacmanSandbox(unittest.TestCase):
    def test_fallback_then_supported_kernel_replaces_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'sandbox.conf'
            sandbox.write_policy(target, 0)
            self.assertIn('\nDisableSandboxFilesystem\n', target.read_text())
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            sandbox.write_policy(target, 11)
            self.assertNotIn('DisableSandboxFilesystem', target.read_text())
            self.assertIn('ABI 11', target.read_text())
            sandbox.write_policy(target, 0)
            self.assertIn('\nDisableSandboxFilesystem\n', target.read_text())
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_failed_update_preserves_last_complete_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'sandbox.conf'
            sandbox.write_policy(target, 0)
            original = target.read_bytes()
            with patch.object(os, 'replace', side_effect=OSError('read-only filesystem')):
                with self.assertRaises(OSError):
                    sandbox.write_policy(target, 11)
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_only_known_unavailable_results_use_compatibility(self):
        for error, result in ((errno.ENOSYS, -1), (errno.EOPNOTSUPP, -1), (0, 1), (0, 12)):
            with self.subTest(result=result, error=error), \
                    patch.object(sandbox.platform, 'machine', return_value='aarch64'), \
                    patch.object(sandbox.ctypes, 'CDLL') as libc, \
                    patch.object(sandbox.ctypes, 'get_errno', return_value=error):
                libc.return_value.syscall.return_value = result
                self.assertEqual(sandbox.landlock_abi(), max(0, result))
        with patch.object(sandbox.platform, 'machine', return_value='unsupported'), \
                patch.object(sandbox.ctypes, 'CDLL') as libc:
            with self.assertRaises(OSError):
                sandbox.landlock_abi()
            libc.assert_not_called()

    def test_failed_probe_clears_stale_compatibility_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'sandbox.conf'
            sandbox.write_policy(target, 0)
            with patch.object(sandbox.platform, 'machine', return_value='aarch64'), \
                    patch.object(sandbox.ctypes, 'CDLL') as libc, \
                    patch.object(sandbox.ctypes, 'get_errno', return_value=errno.EPERM):
                libc.return_value.syscall.return_value = -1
                with self.assertRaises(OSError) as caught:
                    sandbox.refresh_policy(target)
            self.assertEqual(caught.exception.errno, errno.EPERM)
            self.assertNotIn('DisableSandbox', target.read_text())


if __name__ == '__main__':
    unittest.main()
