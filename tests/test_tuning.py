from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import nabu_tuning as tuning


class Tuning(unittest.TestCase):
    def test_unsupported_requests_are_skipped_and_restore_with_a_capable_kernel(self):
        original = 'net.core.default_qdisc=fq\nnet.ipv4.tcp_congestion_control=bbr\nvm.swappiness=150\n'
        disabled = tuning.sysctl_text(original, 'CONFIG_ARM64=y\n')
        self.assertEqual(disabled.count(tuning.PREFIX), 2)
        self.assertIn('\nvm.swappiness=150\n', disabled)
        self.assertEqual(tuning.sysctl_text(disabled, ''), disabled)
        config = 'CONFIG_NET_SCH_FQ=m\nCONFIG_TCP_CONG_BBR=y\n'
        self.assertEqual(tuning.sysctl_text(disabled, config), original)

    def test_custom_values_comments_and_similar_symbols_are_preserved(self):
        text = '# comment\nnet.core.default_qdisc=cake\nnet.ipv4.tcp_congestion_control=cubic\n'
        self.assertEqual(tuning.sysctl_text(text, ''), text)
        text = 'net.core.default_qdisc=fq\n'
        self.assertTrue(tuning.sysctl_text(text, 'CONFIG_NET_SCH_FQ_CODEL=y\n').startswith(tuning.PREFIX))

    def test_only_reviewed_ufs_rule_is_adjusted_and_repeat_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'etc/udev/rules.d/60-omarchy-io-scheduler.rules'
            path.parent.mkdir(parents=True)
            path.write_text('# upstream comment\n'+tuning.IO_RULE+'\n')
            path.chmod(0o640)
            self.assertEqual(tuning.configure(root, ''), [str(path.relative_to(root))])
            self.assertEqual(path.read_text(), '# upstream comment\n'+tuning.UFS_RULE+'\n')
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(tuning.configure(root, ''), [])
            path.write_text(tuning.IO_RULE.replace('kyber', 'bfq')+'\n')
            self.assertEqual(tuning.configure(root, ''), [])

    def test_symlink_file_and_parent_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'root'
            root.mkdir()
            outside = Path(directory)/'outside'
            outside.mkdir()
            (root/'etc').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'leaves'):
                tuning.configure(root, '')
            link = outside/'link'
            target = outside/'target'
            target.write_text('preserve')
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                tuning.update(link, lambda text: 'changed')
            self.assertEqual(target.read_text(), 'preserve')


if __name__ == '__main__':
    unittest.main()
