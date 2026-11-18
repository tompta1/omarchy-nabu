import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProductionKernel(unittest.TestCase):
    def test_normal_candidate_is_pinned_separate_and_does_not_stack_diagnostics(self):
        candidate = json.loads((ROOT/'kernel/profiles/7.3.4-production.json').read_text())
        default = json.loads((ROOT/'kernel/profiles/7.3.4-debug2.json').read_text())
        selected = json.loads((ROOT/'kernel/sources.json').read_text())
        self.assertEqual(selected['boot']['mode'], 'normal')
        for key in ('prepared_tree', 'config_fragment', 'patches', 'boot', 'build'):
            self.assertEqual(selected[key], candidate[key])
        debug6 = json.loads((ROOT/'kernel/profiles/7.3.4-iris-balanced-pm.json').read_text())
        for key in ('commit', 'stable_patch', 'resolution_patch'):
            self.assertEqual(candidate[key], default[key])
        for other in (default, debug6):
            self.assertNotEqual(candidate['source_directory'], other['source_directory'])
            self.assertNotEqual(candidate['prepared_tree'], other['prepared_tree'])
            for key in ('output', 'work_volume'):
                self.assertNotEqual(candidate['build'][key], other['build'][key])
        self.assertNotIn('debug_fragment', candidate)
        self.assertEqual(candidate['boot']['mode'], 'normal')
        self.assertEqual(len(candidate['patches']), 2)
        self.assertFalse(any('trace' in pin['path'] or 'resume.patch' in pin['path'] for pin in candidate['patches']))
        for pin in (candidate['config_fragment'], candidate['boot']['cmdline'], *candidate['patches']):
            self.assertEqual(hashlib.sha256((ROOT/pin['path']).read_bytes()).hexdigest(), pin['sha256'])
        self.assertEqual(candidate['patches'][-1], debug6['patches'][-1])

    def test_normal_boot_keeps_diagnostics_available_without_forced_verbose_logging(self):
        cmdline = (ROOT/'kernel/normal.cmdline').read_text().split()
        for token in ('earlycon=efifb', 'keep_bootcon', 'initcall_debug', 'ignore_loglevel', 'logo.nologo'):
            self.assertNotIn(token, cmdline)
        self.assertIn('drm.debug=0x00', cmdline)
        self.assertIn('loglevel=4', cmdline)
        config = (ROOT/'kernel/normal.config').read_text().splitlines()
        for line in ('CONFIG_LOCALVERSION="-nabu1"', 'CONFIG_SECURITY_LANDLOCK=y',
                     'CONFIG_DEBUG_INFO_DWARF5=y', 'CONFIG_FUNCTION_GRAPH_TRACER=y',
                     'CONFIG_PSTORE_RAM=y', '# CONFIG_GENERIC_IRQ_DEBUGFS is not set',
                     '# CONFIG_KPROBE_EVENTS is not set'):
            self.assertIn(line, config)
        self.assertIn('initcall_debug', (ROOT/'kernel/debug.cmdline').read_text().split())


if __name__ == '__main__':
    unittest.main()
