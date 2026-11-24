"""Personal data must never end up inside the repo or readable by other users."""
import os
import stat
import tempfile
import unittest
from pathlib import Path

from agent.config import ROOT, default_data_dir, inside_git_repo, load_config, private_dir
from agent.store import UserStore


class PrivacyTest(unittest.TestCase):
    def test_default_data_folder_is_outside_the_repo(self):
        data = default_data_dir()
        self.assertFalse(data.resolve().is_relative_to(ROOT), data)
        self.assertEqual(load_config(env_file=Path("/nonexistent/.env")).data_dir, data)

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_user_folders_are_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = UserStore(Path(tmp) / "data", "wa-34600111222")
            for folder in (Path(tmp) / "data", Path(tmp) / "data" / "users", store.dir):
                self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700, folder)

    def test_detects_a_data_folder_inside_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir()
            self.assertTrue(inside_git_repo(private_dir(Path(tmp) / "data")))
            self.assertTrue(inside_git_repo(ROOT / "data"))

    def test_user_ids_cannot_escape_the_data_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = UserStore(Path(tmp), "../../etc")
            self.assertTrue(store.dir.resolve().is_relative_to(Path(tmp).resolve()))


if __name__ == "__main__":
    unittest.main()
