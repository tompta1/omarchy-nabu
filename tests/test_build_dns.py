import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('build_dns',Path(__file__).resolve().parents[1]/'scripts/run-with-build-dns.py')
dns=importlib.util.module_from_spec(spec);spec.loader.exec_module(dns)


class BuildDns(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.base=Path(self.directory.name)
        self.root=self.base/'root';(self.root/'etc').mkdir(parents=True)
        (self.root/'.omarchy-nabu-build-root').touch()
        self.resolver=self.base/'resolver';self.resolver.write_text('nameserver 192.0.2.53\n')
        self.target=self.root/'etc/resolv.conf'

    def test_dangling_runtime_symlink_restored_after_failure(self):
        self.target.symlink_to('/run/systemd/resolve/stub-resolv.conf')
        with self.assertRaisesRegex(RuntimeError,'build failed'):
            with dns.temporary_resolver(self.root,self.resolver):
                self.assertFalse(self.target.is_symlink())
                self.assertEqual(self.target.read_bytes(),self.resolver.read_bytes())
                raise RuntimeError('build failed')
        self.assertEqual(self.target.readlink(),Path('/run/systemd/resolve/stub-resolv.conf'))
        self.assertEqual(list((self.root/'etc').iterdir()),[self.target])

    def test_original_file_or_absence_restored_after_success(self):
        for original in (None,'original runtime DNS\n'):
            if original is not None:self.target.write_text(original)
            with dns.temporary_resolver(self.root,self.resolver):
                self.assertEqual(self.target.read_bytes(),self.resolver.read_bytes())
            if original is None:self.assertFalse(self.target.exists())
            else:self.assertEqual(self.target.read_text(),original)

    def test_unmarked_root_is_rejected_without_changes(self):
        (self.root/'.omarchy-nabu-build-root').unlink()
        with self.assertRaises(ValueError):
            with dns.temporary_resolver(self.root,self.resolver):pass
        self.assertFalse(self.target.exists())


if __name__=='__main__':unittest.main()
