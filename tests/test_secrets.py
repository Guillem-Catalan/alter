"""Connection secrets go to the OS keychain, never to files (ADR 0004). Uses an in-memory keychain."""
import unittest

import keyring
from keyring.backend import KeyringBackend

from agent import secrets


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def get_password(self, service, username):
        return self.items.get((service, username))

    def delete_password(self, service, username):
        if (service, username) not in self.items:
            raise keyring.errors.PasswordDeleteError(username)
        del self.items[(service, username)]


class SecretsTest(unittest.TestCase):
    def setUp(self):
        self.previous = keyring.get_keyring()
        self.memory = MemoryKeyring()
        keyring.set_keyring(self.memory)
        self.addCleanup(keyring.set_keyring, self.previous)

    def test_round_trip_and_delete(self):
        secrets.save("wa-1:linkedin:@ana", {"access_token": "t", "refresh_token": "r"})
        self.assertEqual(self.memory.items[("alter", "wa-1:linkedin:@ana")], '{"access_token": "t", "refresh_token": "r"}')
        self.assertEqual(secrets.load("wa-1:linkedin:@ana")["refresh_token"], "r")
        secrets.delete("wa-1:linkedin:@ana")
        secrets.delete("wa-1:linkedin:@ana")  # deleting twice is fine
        self.assertIsNone(secrets.load("wa-1:linkedin:@ana"))

    def test_no_keychain_is_an_error_not_a_file(self):
        from keyring.backends.fail import Keyring as Fail
        keyring.set_keyring(Fail())
        self.assertIn("No system keychain", secrets.available())
        with self.assertRaises(secrets.SecretsUnavailable):
            secrets.save("x", {"a": 1})


if __name__ == "__main__":
    unittest.main()
