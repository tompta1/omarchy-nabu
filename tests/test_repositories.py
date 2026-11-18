import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from nabu_repositories import apply_policy, database_urls, load_policy


class RepositoryPolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy(ROOT / 'profiles/nabu/repositories.json')

    def test_migrates_inline_nabu_without_losing_user_settings(self):
        original = '''[options]
DownloadUser = alpm
IgnorePkg = local-kernel
DisableSandboxFilesystem
[nabu]
SigLevel = Never
Server = old-nabu-url
[omarchy]
Server = https://pkgs.omarchy.org/edge/$arch
[core]
Include = /etc/pacman.d/mirrorlist
[extra]
Include = /etc/pacman.d/mirrorlist
[alarm]
Include = /etc/pacman.d/mirrorlist
[personal]
Server = https://example.org/$arch
'''
        updated = apply_policy(original, self.policy)
        self.assertNotIn('[nabu]', updated)
        self.assertNotIn('old-nabu-url', updated)
        self.assertIn('IgnorePkg = local-kernel linux-nabu linux-nabu-headers linux-nabu-local linux-nabu-debug linux-nabu-debug-next libssc iio-sensor-proxy', updated)
        self.assertIn('DisableSandboxFilesystem', updated)
        self.assertIn('DownloadUser = alpm', updated)
        sandbox = 'Include = /etc/pacman.d/omarchy-nabu-sandbox.conf'
        self.assertEqual(updated.count(sandbox), 1)
        self.assertLess(updated.index(sandbox), updated.index('[omarchy]'))
        self.assertIn('[personal]\nServer = https://example.org/$arch', updated)
        self.assertIn('https://pkgs.omarchy.org/stable/$arch', updated)
        self.assertTrue(updated.endswith('Include = /etc/pacman.d/nabu.conf\n'))
        self.assertEqual(updated, apply_policy(updated, self.policy))

    def test_refresh_moves_included_nabu_after_upstream_repositories(self):
        original = '[options]\nInclude = /etc/pacman.d/nabu.conf\n[omarchy]\nServer = old\n[extra]\nServer = extra\n'
        updated = apply_policy(original, self.policy)
        self.assertEqual(updated.count('Include = /etc/pacman.d/nabu.conf'), 1)
        self.assertGreater(updated.index('Include = /etc/pacman.d/nabu.conf'), updated.index('[extra]'))

    def test_audit_uses_matching_stable_databases(self):
        urls = dict(database_urls(self.policy))
        self.assertEqual(urls['omarchy'], 'https://pkgs.omarchy.org/stable/aarch64/omarchy.db')
        self.assertEqual(urls['extra'], 'https://arm-stable-mirror.omarchy.org/aarch64/extra/extra.db')
        self.assertEqual(list(urls)[-1], 'nabu')

    def test_only_reviewed_hardware_has_explicit_repository_override(self):
        requests = (ROOT / 'profiles/nabu/hardware.packages').read_text().splitlines()
        overrides = dict(line.split('/', 1)[::-1] for line in requests if '/' in line and not line.startswith('#'))
        self.assertEqual(overrides, self.policy['hardware_overrides'])
        self.assertTrue(set(overrides) <= set(self.policy['held_packages']))
        self.assertTrue(set(self.policy['source_packages']) <= set(self.policy['held_packages']))
        for package in self.policy['source_packages']:
            self.assertTrue((ROOT / 'packages' / package / 'PKGBUILD').is_file())


if __name__ == '__main__':
    unittest.main()
