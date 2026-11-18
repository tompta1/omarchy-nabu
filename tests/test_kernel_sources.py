import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('kernel_sources',ROOT/'scripts/prepare-kernel-source.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class KernelSourcesTest(unittest.TestCase):
    def test_clone_and_dirty_source_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            project=Path(directory)
            cache=project/'upstream/linux-nabu'
            cache.mkdir(parents=True)
            def git(*args):
                return subprocess.check_output(['git','-C',str(cache),*args],text=True).strip()
            git('init','-q')
            git('config','user.name','Kernel fixture')
            git('config','user.email','fixture@example.org')
            (cache/'Makefile').write_text('VERSION = 7\n')
            git('add','Makefile')
            git('commit','-qm','fixture')
            commit=git('rev-parse','HEAD')
            profile=project/'kernel/profile.json'
            profile.parent.mkdir()
            profile.write_text(json.dumps({'repository':str(cache),'commit':commit,
                              'defconfig':'nabu_defconfig','source_directory':'upstream/candidate'}))
            output=project/'out/kernel'
            candidate=module.prepare(project,profile,output)
            self.assertEqual((candidate/'Makefile').read_text(),'VERSION = 7\n')
            (candidate/'Makefile').write_text('user edit\n')
            with self.assertRaisesRegex(ValueError,'modifications'):
                module.prepare(project,profile,output)
            self.assertEqual((candidate/'Makefile').read_text(),'user edit\n')

    def test_patch_checksum_and_path_boundary(self):
        self.assertEqual(module.checked(b'patch',hashlib.sha256(b'patch').hexdigest()),b'patch')
        with self.assertRaises(ValueError):module.checked(b'changed','0'*64)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):module.contained(Path(directory),'../outside')

    def test_additional_patch_tree_and_recheck(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            cache = project/'upstream/linux-nabu'
            cache.mkdir(parents=True)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(cache), *args], text=True).strip()
            git('init', '-q')
            git('config', 'user.name', 'Kernel fixture')
            git('config', 'user.email', 'fixture@example.org')
            makefile = cache/'Makefile'
            makefile.write_text('VERSION = 7\n')
            git('add', '.')
            git('commit', '-qm', 'fixture')
            commit = git('rev-parse', 'HEAD')
            makefile.write_text('VERSION = 8\n')
            patch = subprocess.check_output(['git', '-C', str(cache), 'diff'])
            git('add', '.')
            tree = git('write-tree')
            git('reset', '--hard', 'HEAD')
            profile_path = project/'kernel/profile.json'
            profile_path.parent.mkdir()
            patch_path = project/'kernel/diagnostics.patch'
            patch_path.write_bytes(patch)
            profile = {'repository': str(cache), 'commit': commit,
                       'source_directory': 'upstream/candidate', 'prepared_tree': tree,
                       'patches': [{'path': 'kernel/diagnostics.patch',
                                    'sha256': hashlib.sha256(patch).hexdigest()}]}
            profile_path.write_text(json.dumps(profile))
            output = project/'out/kernel'
            candidate = module.prepare(project, profile_path, output)
            self.assertEqual((candidate/'Makefile').read_text(), 'VERSION = 8\n')
            self.assertEqual((output/'inputs/00-diagnostics.patch').read_bytes(), patch)
            self.assertEqual(module.prepare(project, profile_path, output), candidate)
            patch_path.write_bytes(patch + b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                module.prepare(project, profile_path, output)
            self.assertEqual((candidate/'Makefile').read_text(), 'VERSION = 8\n')
            patch_path.write_bytes(patch)
            profile.pop('prepared_tree')
            profile['source_directory'] = 'upstream/no-tree'
            profile_path.write_text(json.dumps(profile))
            with self.assertRaisesRegex(ValueError, 'pinned resulting tree'):
                module.prepare(project, profile_path, output)


if __name__=='__main__':unittest.main()
