import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class IrisDiagnosticProfileTest(unittest.TestCase):
    def test_balanced_pm_replaces_failed_patch_and_is_isolated(self):
        candidate = json.loads((ROOT/'kernel/profiles/7.3.4-iris-balanced-pm.json').read_text())
        default = json.loads((ROOT/'kernel/sources.json').read_text())
        trace = json.loads((ROOT/'kernel/profiles/7.3.4-iris-pm-trace.json').read_text())
        failed = json.loads((ROOT/'kernel/profiles/7.3.4-iris-resume.json').read_text())
        for key in ('commit', 'stable_patch', 'resolution_patch'):
            self.assertEqual(candidate[key], default[key])
        self.assertEqual(candidate['patches'][:len(trace['patches'])], trace['patches'])
        self.assertNotIn(failed['patches'][-1]['path'],
                         [pin['path'] for pin in candidate['patches']])
        for other in (default, trace, failed):
            for key in ('source_directory', 'prepared_tree'):
                self.assertNotEqual(candidate[key], other[key])
            for key in ('output', 'work_volume'):
                self.assertNotEqual(candidate['build'][key], other['build'][key])
        for pin in [candidate['debug_fragment'], *candidate['patches']]:
            self.assertEqual(hashlib.sha256((ROOT/pin['path']).read_bytes()).hexdigest(), pin['sha256'])
        self.assertIn('CONFIG_LOCALVERSION="-nabu-debug6"',
                      (ROOT/candidate['debug_fragment']['path']).read_text().splitlines())

    def test_resume_candidate_preserves_base_and_keeps_diagnostics(self):
        default = json.loads((ROOT/'kernel/sources.json').read_text())
        trace = json.loads((ROOT/'kernel/profiles/7.3.4-iris-pm-trace.json').read_text())
        resume = json.loads((ROOT/'kernel/profiles/7.3.4-iris-resume.json').read_text())
        for key in ('commit', 'stable_patch', 'resolution_patch'):
            self.assertEqual(resume[key], default[key])
        self.assertEqual(resume['patches'][:-1], trace['patches'])
        for key in ('source_directory', 'prepared_tree'):
            self.assertNotEqual(resume[key], trace[key])
        for key in ('output', 'work_volume'):
            self.assertNotEqual(resume['build'][key], trace['build'][key])
            self.assertNotEqual(resume['build'][key], default['build'][key])
        for pin in [resume['debug_fragment'], *resume['patches']]:
            self.assertEqual(hashlib.sha256((ROOT/pin['path']).read_bytes()).hexdigest(), pin['sha256'])
        self.assertIn('CONFIG_LOCALVERSION="-nabu-debug5"',
                      (ROOT/resume['debug_fragment']['path']).read_text().splitlines())

    def test_pm_trace_profile_is_separate_and_patch_is_pinned(self):
        default = json.loads((ROOT/'kernel/sources.json').read_text())
        diagnostics = json.loads((ROOT/'kernel/profiles/7.3.4-iris-diagnostics.json').read_text())
        trace = json.loads((ROOT/'kernel/profiles/7.3.4-iris-pm-trace.json').read_text())
        for key in ('commit', 'stable_patch', 'resolution_patch'):
            self.assertEqual(trace[key], default[key])
        for other in (default, diagnostics):
            self.assertNotEqual(trace['prepared_tree'], other['prepared_tree'])
            self.assertNotEqual(trace['source_directory'], other['source_directory'])
            self.assertNotEqual(trace['build']['output'], other['build']['output'])
            self.assertNotEqual(trace['build']['work_volume'], other['build']['work_volume'])
        for pin in [trace['debug_fragment'], *trace['patches']]:
            self.assertEqual(hashlib.sha256((ROOT/pin['path']).read_bytes()).hexdigest(), pin['sha256'])
        self.assertIn('CONFIG_LOCALVERSION="-nabu-debug4"',
                      (ROOT/trace['debug_fragment']['path']).read_text().splitlines())

    def test_diagnostic_profile_is_pinned_and_separate(self):
        default = json.loads((ROOT/'kernel/profiles/7.3.4-debug2.json').read_text())
        candidate = json.loads((ROOT/'kernel/profiles/7.3.4-iris-diagnostics.json').read_text())
        self.assertEqual(candidate['commit'], default['commit'])
        self.assertEqual(candidate['prepared_tree'], default['prepared_tree'])
        self.assertNotEqual(candidate['build']['output'], default['build']['output'])
        self.assertNotEqual(candidate['build']['work_volume'], default['build']['work_volume'])
        fragment = candidate['debug_fragment']
        content = (ROOT/fragment['path']).read_bytes()
        self.assertEqual(hashlib.sha256(content).hexdigest(), fragment['sha256'])
        lines = content.decode().splitlines()
        for required in ('CONFIG_LOCALVERSION="-nabu-debug3"', 'CONFIG_GENERIC_IRQ_DEBUGFS=y',
                         'CONFIG_KPROBES=y', 'CONFIG_KPROBE_EVENTS=y',
                         'CONFIG_FUNCTION_GRAPH_RETVAL=y'):
            self.assertIn(required, lines)
        self.assertIn('CONFIG_LOCALVERSION="-nabu-debug2"', (ROOT/'kernel/debug.config').read_text().splitlines())


if __name__ == '__main__':
    unittest.main()
