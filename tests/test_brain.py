import unittest

from agent.brain import split_drafts
from agent.store import UserStore

from tests.helpers import make_agent

SETUP_REPLY = "[SETUP: Ana | @ana on X | Builds a CRM]Nice to meet you, Ana!"


class AgentTest(unittest.TestCase):
    def start(self, *replies, **kwargs):
        agent, llm, tmp = make_agent(SETUP_REPLY, *replies, **kwargs)
        self.addCleanup(tmp.cleanup)
        self.assertIn("TestBot", agent.handle("u1", "hi").text)  # welcome message, no model call
        self.assertEqual(llm.calls, [])
        self.assertEqual(agent.handle("u1", "I'm Ana, @ana, building a CRM").text, "Nice to meet you, Ana!")
        self.store = UserStore(agent.data_dir, "u1")
        return agent, llm

    def test_setup_extracts_profile(self):
        self.start()
        self.assertEqual(self.store.profile()["name"], "Ana")
        self.assertEqual(self.store.profile()["handles"], "@ana on X")

    def test_tags_are_stored_and_stripped(self):
        agent, _ = self.start(
            "Got it [LEARN: no emojis] [PUBLISHED: linkedin | Shipped v2 today] "
            "[VOICE: my old post] here you go"
        )
        reply = agent.handle("u1", "posted it on linkedin, and never use emojis").text
        self.assertEqual(reply, "Got it here you go")
        self.assertEqual(self.store.preferences(), ["no emojis"])
        self.assertEqual(self.store.published_today()[0]["network"], "linkedin")
        self.assertIn("my old post", self.store.voice_pack())

    def test_skill_is_loaded_then_answered(self):
        agent, llm = self.start("[SKILL: li-post]", "Here are your hooks")
        reply = agent.handle("u1", "LinkedIn post about our launch").text
        self.assertEqual(reply, "Here are your hooks")
        self.assertEqual(self.store.active_skills(), ["li-post"])
        system_after = "\n".join(llm.calls[-1][0])
        self.assertIn('<skill name="li-post"', system_after)
        # The skill request itself never reaches the history.
        self.assertNotIn("[SKILL", str(self.store.history()))

    def test_already_loaded_skill_does_not_loop(self):
        agent, llm = self.start("[SKILL: x-post]", "[SKILL: x-post]", "Drafts!")
        self.assertEqual(agent.handle("u1", "a tweet please").text, "Drafts!")
        self.assertIn("already loaded", llm.calls[-1][0][-1])

    def test_unknown_skill_is_ignored(self):
        agent, _ = self.start("Sure [SKILL: does-not-exist]")
        self.assertEqual(agent.handle("u1", "x").text, "Sure")

    def test_commands_do_not_call_the_model(self):
        agent, llm = self.start()
        calls = len(llm.calls)
        self.assertIn("haven't learned", agent.handle("u1", "preferences").text)
        self.assertIn("x-post", agent.handle("u1", "skills").text)
        self.assertIn("Ana", agent.handle("u1", "profile").text)
        self.assertEqual(len(llm.calls), calls)

    def test_history_starts_with_user_and_is_capped(self):
        agent, llm = self.start(*["reply"] * 40)
        for i in range(40):
            agent.handle("u1", f"message {i}")
        history = self.store.history()
        self.assertLessEqual(len(history), 30)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(llm.calls[-1][1][0]["role"], "user")

    def test_drafts_are_humanized(self):
        agent, _ = self.start("Option:\n```\nIt was seamless — truly.\n```", humanize=True)
        reply = agent.handle("u1", "tweet").text
        self.assertNotIn("—", reply)

    def test_files_in_and_out(self):
        from agent.files import ingest
        agent, llm = self.start(*["[FILE: plan.md]\n# Plan\n[/FILE]Done."] + ["ok"] * 4)
        user_dir = self.store.dir
        png = ingest(b"\x89PNG fake", "image/png", "shot.png", user_dir, None, ffmpeg="")
        reply = agent.handle("u1", "plan from this", attachments=[png])
        self.assertEqual(reply.text, "Done.")
        self.assertEqual([f.name for f in reply.files], ["plan.md"])
        self.assertEqual(reply.files[0].read_text(), "# Plan\n")
        self.assertIn("[sent file: plan.md]", self.store.history()[-1]["content"])
        # After three newer messages with files, the first image is sent as a note, not as bytes.
        for _ in range(3):
            agent.handle("u1", "again", attachments=[ingest(b"\x89PNG", "image/png", "s.png", user_dir, None, ffmpeg="")])
        first = next(m for m in llm.calls[-1][1] if m.get("attachments") and m["attachments"][0]["name"] == "shot.png")
        self.assertNotIn("data", first["attachments"][0])
        self.assertIn("data", llm.calls[-1][1][-1]["attachments"][0])

    def test_spanish_texts(self):
        agent, _, tmp = make_agent(language="es")
        self.addCleanup(tmp.cleanup)
        self.assertIn("Hola", agent.handle("u2", "hola").text)


class SplitDraftsTest(unittest.TestCase):
    def test_each_draft_is_its_own_message(self):
        text = "Two options:\n```\nFirst post\n```\n```text\nSecond post\n```\nI'd go with the first."
        self.assertEqual(split_drafts(text), ["Two options:", "First post", "Second post", "I'd go with the first."])

    def test_plain_text(self):
        self.assertEqual(split_drafts("just chat"), ["just chat"])


if __name__ == "__main__":
    unittest.main()
