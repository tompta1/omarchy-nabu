import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

path = Path(__file__).resolve().parents[1] / 'scripts/omarchy-nabu-iris-recover'
loader = importlib.machinery.SourceFileLoader('iris_recover', str(path))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'firmware/devicetree/base').mkdir(parents=True)
        (self.root/'firmware/devicetree/base/compatible').write_bytes(b'xiaomi,nabu\0qcom,sm8150\0')
        self.mod = self.root/'module/qcom_iris'
        (self.mod/'parameters').mkdir(parents=True)
        (self.mod/'refcnt').write_text('0')
        (self.mod/'parameters/allow_fw_boot').write_text('Y')
        (self.mod/'parameters/cached_capture').write_text('N')
        driver = self.root/'bus/platform/drivers/qcom-iris'
        driver.mkdir(parents=True)
        device = self.root/'bus/platform/devices/aa00000.video-codec'
        device.mkdir(parents=True)
        (device/'driver').symlink_to(driver)
        self.commands = []

    def run_command(self, cmd, check):
        self.commands.append((cmd, check))
        return subprocess.CompletedProcess(cmd, 0)

    def test_restores_parameters(self):
        module.recover(self.root, self.run_command)
        self.assertEqual(self.commands[0][0], ['/usr/bin/rmmod', 'qcom_iris'])
        self.assertEqual(self.commands[1][0], ['/usr/bin/modprobe', 'qcom_iris', 'allow_fw_boot=1', 'cached_capture=0'])

    def test_busy_or_unrecognized_parameters_refuse_before_removal(self):
        (self.mod/'refcnt').write_text('1')
        with self.assertRaises(ValueError):
            module.recover(self.root, self.run_command)
        (self.mod/'refcnt').write_text('0')
        (self.mod/'parameters/unknown').write_text('1')
        with self.assertRaises(ValueError):
            module.recover(self.root, self.run_command)
        self.assertEqual(self.commands, [])

    def test_removal_failure_never_forces_unload(self):
        def fail(cmd, check):
            self.commands.append((cmd, check))
            raise subprocess.CalledProcessError(1, cmd)
        with self.assertRaises(subprocess.CalledProcessError):
            module.recover(self.root, fail)
        self.assertEqual(len(self.commands), 1)

    def test_failed_load_attempts_restore(self):
        def fail_once(cmd, check):
            self.commands.append((cmd, check))
            if len(self.commands) == 2:
                raise subprocess.CalledProcessError(1, cmd)
        with self.assertRaises(subprocess.CalledProcessError):
            module.recover(self.root, fail_once)
        self.assertEqual(self.commands[2][0], self.commands[1][0])
        self.assertFalse(self.commands[2][1])


if __name__ == '__main__':
    unittest.main()
