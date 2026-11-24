import unittest

from agent.config import ROOT
from agent.skills import SkillLibrary, load_humanizer, parse_frontmatter


class SkillLibraryTest(unittest.TestCase):
    def setUp(self):
        self.library = SkillLibrary([ROOT / "skills"])

    def test_finds_bundled_skills_for_every_network(self):
        for name in ("x-post", "li-post", "li-human", "li-audit", "ig-reel", "ig-audit", "yt-script", "yt-shorts"):
            self.assertIn(name, self.library)
        for skill in self.library.skills.values():
            self.assertTrue(skill.description, f"{skill.name} has no description")

    def test_body_includes_data_files_but_not_scripts(self):
        body = self.library.get("li-post").body()
        self.assertIn('<file name="hooks.json">', body)
        self.assertNotIn("---\nname:", body)
        human = self.library.get("li-human").body()
        self.assertIn('<file name="slop.json">', human)
        self.assertNotIn("def humanize", human)

    def test_folded_description(self):
        meta = parse_frontmatter("---\nname: a\ndescription: >-\n  one\n  two\n---\nbody")
        self.assertEqual(meta, {"name": "a", "description": "one two"})

    def test_humanizer_cleans_machine_fingerprints(self):
        clean = load_humanizer(self.library)
        self.assertIsNotNone(clean)
        out = clean("We shipped it — and it was seamless.​")
        self.assertNotIn("—", out)
        self.assertNotIn("​", out)


if __name__ == "__main__":
    unittest.main()
