import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import nabu_paths
import nabu_image_options
from nabu_sources import load_sources,scoped_sources
spec=importlib.util.spec_from_file_location('reuse_root',ROOT/'scripts/reuse-prepared-root.py')
reuse_root=importlib.util.module_from_spec(spec)
spec.loader.exec_module(reuse_root)


class SourceScopes(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        self.source=self.root/'out/source'; self.source.mkdir(parents=True)
        self.output=self.root/'out/reused'; self.output.mkdir()
        self.os_pin={'omarchy':{'url':'https://example.org/omarchy.git','commit':'1'*40}}
        self.new_pins={**self.os_pin,'uboot':{'url':'https://example.org/uboot.git','commit':'2'*40,'scope':'bootloader'}}
        (self.root/'sources.json').write_text(json.dumps(self.new_pins))
        (self.source/'sources.json').write_text(json.dumps(self.os_pin))
        (self.root/'local').mkdir()
        parameters={}
        self.environment={}
        for name in ('NABU_LAYOUT','NABU_PROVISION'):
            relative='local/'+name.lower()+'.json'
            (self.root/relative).write_text('{}')
            self.environment[name]=relative
            parameters[name+'_sha256']=hashlib.sha256(b'{}').hexdigest()
        (self.source/'build-parameters.json').write_text(json.dumps(parameters))
        (self.source/'work-volume.txt').write_text('prepared-os-volume\n')
        for name in ('packages.lock','bootstrap.sha256','keyboard-source.json','desktop-smoke.log'):
            (self.source/name).write_text('fixture input\n')
        (self.source/'device-packages').mkdir()
        policy = ROOT/'profiles/nabu/repositories.json'
        target_policy = self.root/'profiles/nabu/repositories.json'
        target_policy.parent.mkdir(parents=True)
        target_policy.write_bytes(policy.read_bytes())
        for name in json.loads(policy.read_text())['source_packages']:
            recipe = self.root/'packages'/name/'PKGBUILD'
            recipe.parent.mkdir(parents=True)
            recipe.write_text('pkgrel=1\n')
            (self.source/'device-packages'/(name+'.PKGBUILD')).write_bytes(recipe.read_bytes())

    def reuse(self):
        with patch.object(nabu_paths,'PROJECT',self.root),patch.object(reuse_root,'PROJECT',self.root), \
                patch.dict(os.environ,self.environment):
            return reuse_root.reuse(self.source,self.output)

    def test_bootloader_pin_does_not_invalidate_prepared_os(self):
        self.assertEqual(self.reuse(),'prepared-os-volume')
        self.assertEqual(json.loads((self.output/'sources.json').read_text()),self.os_pin)
        record=json.loads((self.output/'root-provenance.json').read_text())
        self.assertEqual(record['separate_bootloader_inputs']['uboot'],self.new_pins['uboot'])
        self.assertEqual(set(scoped_sources(self.new_pins)),{'omarchy'})
        self.assertEqual(set(scoped_sources(self.new_pins,'bootloader')),{'uboot'})

    def test_changed_os_pin_still_blocks_reuse(self):
        self.new_pins['omarchy']={**self.os_pin['omarchy'],'commit':'3'*40}
        (self.root/'sources.json').write_text(json.dumps(self.new_pins))
        with self.assertRaisesRegex(ValueError,'different upstream source pins'): self.reuse()
        self.assertEqual(list(self.output.iterdir()),[])

    def test_device_recipe_change_blocks_reuse(self):
        (self.root/'packages/iio-sensor-proxy/PKGBUILD').write_text('pkgrel=2\n')
        with self.assertRaisesRegex(ValueError, 'different package recipe: iio-sensor-proxy'):
            self.reuse()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_reuse_records_source_options_and_rejects_tampered_selection(self):
        selected = {**nabu_image_options.DEFAULT, 'screensaver_touch_dismiss': True}
        path = self.source/'image-options.json'
        path.write_bytes(nabu_image_options.encoded(selected))
        parameters_path = self.source/'build-parameters.json'
        parameters = json.loads(parameters_path.read_text())
        parameters['image_options_sha256'] = nabu_image_options.digest(selected)
        parameters_path.write_text(json.dumps(parameters))
        self.assertEqual(self.reuse(), 'prepared-os-volume')
        record = json.loads((self.output/'root-provenance.json').read_text())
        self.assertEqual(record['source_image_options'], selected)
        self.assertTrue(record['desktop_reconfiguration_required'])
        path.write_bytes(nabu_image_options.encoded(nabu_image_options.DEFAULT))
        with self.assertRaisesRegex(ValueError, 'option selection'):
            self.reuse()

    def test_invalid_source_scope_or_path_is_rejected(self):
        for name,scope in (('../outside','bootloader'),('uboot','unknown')):
            path=self.root/'invalid.json'
            path.write_text(json.dumps({name:{'url':'https://example.org/code.git','commit':'1'*40,'scope':scope}}))
            with self.subTest(name=name,scope=scope),self.assertRaises(ValueError): load_sources(path)


if __name__=='__main__': unittest.main()
