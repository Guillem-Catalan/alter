import unittest

from agent.brain import split_drafts
from agent.files import ingest
from agent.llm import Turn, UsageLimitError
from agent.store import UserStore
from agent.tools import ToolCall

from tests.helpers import call, make_agent

SETUP = [call("set_profile", name="Ana", handles="@ana on X", context="Builds a CRM"), "Nice to meet you, Ana!"]


class AgentTest(unittest.TestCase):
    def start(self, *turns, **kwargs):
        agent, llm, tmp = make_agent(*SETUP, *turns, **kwargs)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(agent.db.close)
        self.assertIn("TestBot", agent.handle("u1", "hi").text)  # welcome message, no model call
        self.assertEqual(llm.calls, [])
        self.assertEqual(agent.handle("u1", "I'm Ana, @ana, building a CRM").text, "Nice to meet you, Ana!")
        self.store = UserStore(agent.data_dir, "u1")
        return agent, llm

    def tool_results(self, llm, index=-1):
        return next(m for m in reversed(llm.calls[index][1]) if m["role"] == "tool_results")["results"]

    def test_setup_saves_profile_through_a_tool(self):
        self.start()
        self.assertEqual(self.store.profile()["name"], "Ana")
        self.assertEqual(self.store.profile()["handles"], "@ana on X")

    def test_memory_tools_in_one_turn(self):
        agent, llm = self.start(Turn("", [
            ToolCall("a", "remember_preference", {"preference": "no emojis"}),
            ToolCall("b", "log_published", {"network": "linkedin", "text": "Shipped v2 today"}),
            ToolCall("c", "save_voice_sample", {"text": "my old post"}),
        ]), "Got it")
        self.assertEqual(agent.handle("u1", "posted it on linkedin, and never use emojis").text, "Got it")
        self.assertEqual(self.store.preferences(), ["no emojis"])
        self.assertEqual(self.store.published_today()[0]["network"], "linkedin")
        self.assertIn("my old post", self.store.voice_pack())
        self.assertEqual([r["is_error"] for r in self.tool_results(llm)], [False, False, False])

    def test_skill_is_loaded_and_stays(self):
        agent, llm = self.start(call("load_skill", name="li-post"), "Here are your hooks", "More")
        self.assertEqual(agent.handle("u1", "LinkedIn post about our launch").text, "Here are your hooks")
        self.assertEqual(self.store.active_skills(), ["li-post"])
        self.assertIn("hooks.json", self.tool_results(llm)[0]["content"])
        agent.handle("u1", "another one")
        self.assertIn('<skill name="li-post"', "\n".join(llm.calls[-1][0]))

    def test_one_voice_profile_for_every_channel(self):
        profile = "## Tone\nCasual, build in public.\n## Never says\nleverage, synergy"
        agent, llm = self.start(call("update_voice_profile", profile=profile), "Noted", "ok")
        agent.handle("u1", "I never say leverage, write like I talk")
        self.assertEqual(self.store.voice_profile(), profile + "\n")
        agent.handle("u1", "a LinkedIn post and a tweet about it")
        system = "\n".join(llm.calls[-1][0])
        self.assertIn(f"<voice_profile>\n{profile}", system)
        self.assertIn("Never keep a separate voice per network", system)
        self.assertIn("email is more formal", system)
        self.assertIn("Never hold back a draft to ask for samples", system)

    def test_voice_profile_is_requested_when_due(self):
        from agent.store import VOICE_FIRST_AFTER, VOICE_REFRESH_EVERY
        agent, llm = self.start(*["ok"] * 40)
        due = lambda: "VOICE PROFILE DUE" in llm.calls[-1][0][1]
        agent.handle("u1", "one")  # the setup message counted as the first
        self.assertEqual(due(), 2 >= VOICE_FIRST_AFTER)
        agent.handle("u1", "two")
        self.assertTrue(due())
        self.store.save_voice_profile("## Tone\nCasual and direct, short sentences, build in public.")
        agent.handle("u1", "three")
        self.assertFalse(due())
        for i in range(VOICE_REFRESH_EVERY):
            agent.handle("u1", f"more {i}")
        self.assertTrue(due())

    def test_voice_profile_keeps_previous_versions(self):
        from agent.store import VOICE_HISTORY_LIMIT
        self.start()
        for i in range(VOICE_HISTORY_LIMIT + 3):
            self.store.save_voice_profile(f"version {i}")
        versions = sorted((self.store.dir / "voice_profile_history").glob("*.md"))
        self.assertEqual(len(versions), VOICE_HISTORY_LIMIT)
        self.assertEqual(versions[-1].read_text(), f"version {VOICE_HISTORY_LIMIT + 1}\n")
        self.assertEqual(self.store.voice_profile(), f"version {VOICE_HISTORY_LIMIT + 2}\n")

    def test_voice_profile_too_short_is_rejected(self):
        agent, llm = self.start(call("update_voice_profile", profile="casual"), "ok")
        agent.handle("u1", "remember my voice")
        self.assertEqual(self.store.voice_profile(), "")
        self.assertTrue(self.tool_results(llm)[0]["is_error"])

    def test_unknown_tool_and_bad_input_go_back_as_errors(self):
        agent, llm = self.start(Turn("", [ToolCall("a", "fly_to_mars", {}),
                                          ToolCall("b", "log_published", {"network": "myspace", "text": "x"})]), "Sorry")
        self.assertEqual(agent.handle("u1", "x").text, "Sorry")
        results = self.tool_results(llm)
        self.assertTrue(all(r["is_error"] for r in results))
        self.assertIn("must be one of", results[1]["content"])

    def test_text_written_with_tool_calls_is_kept(self):
        agent, _ = self.start(Turn("Here is the email:\n```\nHi Sam, it works.\n```", [
            ToolCall("v", "remember_preference", {"preference": "short emails"})]), "Saved your style too.")
        reply = agent.handle("u1", "short email to my partner")
        self.assertIn("Hi Sam, it works.", reply.text)
        self.assertTrue(reply.text.endswith("Saved your style too."))

    def test_tool_loop_is_capped(self):
        agent, llm = self.start(*[call("capabilities")] * 20)
        agent.handle("u1", "loop forever")
        self.assertEqual(len(llm.calls), 2 + 8)  # setup turns + the cap

    def test_create_file(self):
        agent, _ = self.start(call("create_file", filename="plan.md", content="# Plan"), "Done.")
        reply = agent.handle("u1", "plan please")
        self.assertEqual(reply.text, "Done.")
        self.assertEqual([f.name for f in reply.files], ["plan.md"])
        self.assertIn("[sent file: plan.md]", self.store.history()[-1]["content"])

    def test_capabilities_are_honest_without_connections(self):
        agent, llm = self.start(call("capabilities"), "I can't publish yet.")
        agent.handle("u1", "publish this on linkedin")
        text = self.tool_results(llm)[0]["content"]
        self.assertIn("No accounts are connected", text)
        self.assertIn("cannot publish", text)

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
        self.assertNotIn("—", agent.handle("u1", "tweet").text)

    def test_usage_limit_is_relayed(self):
        agent, llm = self.start()

        def limited(*_):
            raise UsageLimitError("You've hit your limit · resets 6pm")
        llm.respond = limited
        self.assertIn("resets 6pm", agent.handle("u1", "hello").text)

    def test_attachments_inline_only_for_recent_messages(self):
        agent, llm = self.start(*["ok"] * 5)
        png = ingest(b"\x89PNG fake", "image/png", "shot.png", self.store.dir, None, ffmpeg="")
        agent.handle("u1", "look", attachments=[png])
        for _ in range(3):
            agent.handle("u1", "again", attachments=[ingest(b"\x89PNG", "image/png", "s.png", self.store.dir, None, ffmpeg="")])
        first = next(m for m in llm.calls[-1][1] if m.get("attachments") and m["attachments"][0]["name"] == "shot.png")
        self.assertNotIn("data", first["attachments"][0])
        self.assertIn("data", llm.calls[-1][1][-1]["attachments"][0])

    def test_spanish_texts(self):
        agent, _, tmp = make_agent(language="es")
        self.addCleanup(tmp.cleanup)
        self.addCleanup(agent.db.close)
        self.assertIn("¿cómo te llamas", agent.handle("u2", "hola").text)


class SplitDraftsTest(unittest.TestCase):
    def test_each_draft_is_its_own_message(self):
        text = "Two options:\n```\nFirst post\n```\n```text\nSecond post\n```\nI'd go with the first."
        self.assertEqual(split_drafts(text), ["Two options:", "First post", "Second post", "I'd go with the first."])

    def test_plain_text(self):
        self.assertEqual(split_drafts("just chat"), ["just chat"])
        self.assertEqual(split_drafts("one\n\ntwo"), ["one\n\ntwo"])

    def test_bubbles(self):
        text = "Nice one\n\nHere you go:\n```\nPost\n```\nA\n\nB\n\nC\n\nD\n\nE"
        self.assertEqual(split_drafts(text, bubbles=4), ["Nice one", "Here you go:", "Post", "A", "B", "C", "D\n\nE"])

    def test_profile_without_handles(self):
        agent, llm, tmp = make_agent(call("set_profile", name="Ana", context="Building a CRM"), "Nice, Ana")
        self.addCleanup(tmp.cleanup)
        self.addCleanup(agent.db.close)
        agent.handle("u3", "hi")
        agent.handle("u3", "Ana, building a CRM")
        self.assertEqual(UserStore(agent.data_dir, "u3").profile()["name"], "Ana")


if __name__ == "__main__":
    unittest.main()
