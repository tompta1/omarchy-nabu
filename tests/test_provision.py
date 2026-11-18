import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nabu_provision', ROOT/'scripts/nabu_provision.py')
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


class ProvisionValidation(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.hash = self.root/'login.hash'
        self.hash.write_text('$6$testsalt$'+'A'*86+'\n')
        self.config = self.root/'provision.json'
        self.data = {'username': 'nabu', 'password_hash_file': str(self.hash),
                     'authorized_keys_file': None, 'autologin': False}

    def load(self):
        self.config.write_text(json.dumps(self.data))
        with patch.dict(os.environ, {'NABU_PROVISION': str(self.config)}):
            return provision.load_provision()

    def test_explicit_account_without_ssh_or_autologin(self):
        config = self.load()
        self.assertEqual(config['username'], 'nabu')
        self.assertEqual(config['authorized_keys'], [])
        self.assertFalse(config['autologin'])

    def test_no_plaintext_or_lock_marker_accepted_as_password(self):
        for value in ['plaintext', '!', '*', '$6$truncated']:
            with self.subTest(value=value):
                self.hash.write_text(value)
                with self.assertRaises(SystemExit):
                    self.load()

    def test_invalid_user_and_implicit_autologin_are_rejected(self):
        for key, value in [('username', 'root'), ('username', 'name\nother'),
                           ('username', 'bad:name'), ('autologin', 'yes')]:
            with self.subTest(key=key, value=value):
                original = self.data[key]
                self.data[key] = value
                with self.assertRaises(SystemExit):
                    self.load()
                self.data[key] = original

    def test_bad_keys_rejected_without_quoting_their_content(self):
        keys = self.root/'keys'
        secret = 'not-a-public-key-private-test-content'
        keys.write_text(secret)
        self.data['authorized_keys_file'] = str(keys)
        with self.assertRaises(SystemExit) as result:
            self.load()
        self.assertNotIn(secret, str(result.exception))


if __name__ == '__main__':
    unittest.main()
