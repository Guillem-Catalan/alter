"""The Claude Code provider against a fake `claude` executable (no real calls, no credentials)."""
import json
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agent.config import load_config
from agent.llm import ClaudeCodeLLM, FallbackLLM, LLMError, Turn, UsageLimitError, make_llm
from agent.tools import Tool, obj

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
request = json.loads(sys.stdin.readline())
record = {"args": args, "request": request, "has_api_key": "ANTHROPIC_API_KEY" in os.environ, "cwd": os.getcwd()}
open(os.environ["FAKE_CLAUDE_LOG"], "w").write(json.dumps(record))
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
print(json.dumps({"type": "system", "subtype": "init"}))
if mode == "ok":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output":
        {"text": "hola", "tool_calls": [{"name": "capabilities", "input": {}}]}}))
elif mode == "limit":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "api_error_status": 429,
                      "result": "Claude usage limit reached. Your limit will reset at 6pm."}))
elif mode == "logout":
    print("Invalid API key · Please run /login", file=sys.stderr)
    sys.exit(1)
'''


class ClaudeCodeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        fake = self.dir / "claude"
        fake.write_text(FAKE)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.log = self.dir / "log.json"
        self.config = replace(load_config(env_file=Path("/nonexistent/.env")), llm_provider="claude-code",
                              llm_model="sonnet", claude_bin=str(fake), llm_fallback_provider="")
        env = {"FAKE_CLAUDE_LOG": str(self.log), "ANTHROPIC_API_KEY": "must-not-leak"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tool = Tool("capabilities", "List what you can do.", obj({}), "read", run=lambda c, a: "x")

    def respond(self, messages=None):
        llm = ClaudeCodeLLM(self.config)
        return llm.respond(["system rules"], messages or [{"role": "user", "content": "hi"}], [self.tool])

    def test_tool_calls_come_back_as_a_turn(self):
        turn = self.respond()
        self.assertEqual(turn.text, "hola")
        self.assertEqual([(c.name, c.input) for c in turn.tool_calls], [("capabilities", {})])
        record = json.loads(self.log.read_text())
        args = record["args"]
        self.assertFalse(record["has_api_key"])  # the user's plan is used, never an API key
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertEqual(args[args.index("--setting-sources") + 1], "")
        self.assertIn("--strict-mcp-config", args)
        self.assertIn("--no-session-persistence", args)
        system = args[args.index("--system-prompt") + 1]
        self.assertIn("system rules", system)
        self.assertIn("- capabilities: List what you can do.", system)
        self.assertNotEqual(Path(record["cwd"]).resolve(), Path.cwd().resolve())

    def test_conversation_images_and_tool_results_are_sent(self):
        self.respond([
            {"role": "user", "content": "look", "attachments": [
                {"kind": "image", "name": "a.png", "mime": "image/png", "text": "", "data": "QUJD"}]},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "name": "capabilities", "input": {}}]},
            {"role": "tool_results", "results": [{"id": "1", "name": "capabilities", "content": "lots", "is_error": False}]},
        ])
        content = json.loads(self.log.read_text())["request"]["message"]["content"]
        self.assertEqual(content[1], {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}})
        transcript = "".join(b.get("text", "") for b in content)
        self.assertIn("USER: look", transcript)
        self.assertIn("ASSISTANT called capabilities with {}", transcript)
        self.assertIn("TOOL RESULT (capabilities): lots", transcript)

    def test_usage_limit(self):
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_MODE": "limit"}), self.assertRaises(UsageLimitError) as e:
            self.respond()
        self.assertIn("reset at 6pm", str(e.exception))

    def test_not_signed_in(self):
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_MODE": "logout"}), self.assertRaises(LLMError) as e:
            self.respond()
        self.assertIn("not signed in", str(e.exception))

    def test_missing_cli(self):
        with self.assertRaises(LLMError):
            ClaudeCodeLLM(replace(self.config, claude_bin=str(self.dir / "nope")))

    def test_fallback_on_limit(self):
        class Spare:
            def respond(self, *a):
                return Turn("from the API")
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_MODE": "limit"}):
            llm = FallbackLLM(ClaudeCodeLLM(self.config), Spare())
            self.assertEqual(llm.respond(["s"], [{"role": "user", "content": "hi"}], []).text, "from the API")

    def test_make_llm_wires_the_fallback(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            llm = make_llm(replace(self.config, llm_fallback_provider="anthropic"))
        self.assertIsInstance(llm, FallbackLLM)
        self.assertIsInstance(llm.primary, ClaudeCodeLLM)


if __name__ == "__main__":
    unittest.main()
