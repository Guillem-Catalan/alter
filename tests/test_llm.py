"""The real SDK against a local fake of the Messages API: checks what we send and how we read the answer."""
import json
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from agent.config import load_config
from agent.llm import ClaudeLLM, LLMError, OpenAICompatibleLLM, UsageLimitError
from agent.tools import Tool, obj


class FakeAPI:
    def __init__(self, response: dict):
        self.requests: list[tuple[str, dict, dict]] = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                api.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                raw = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def message(text: str, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
        "content": [{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "text", "text": text}],
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class LLMTest(unittest.TestCase):
    def config(self, **overrides):
        return replace(load_config(env_file=Path("/nonexistent/.env")), **overrides)

    def fake(self, response):
        api = FakeAPI(response)
        self.addCleanup(api.server.server_close)
        self.addCleanup(api.server.shutdown)
        return api

    def test_anthropic_request_shape(self):
        api = self.fake(message("hello"))
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": api.url}):
            llm = ClaudeLLM(self.config(llm_provider="anthropic", llm_model="claude-opus-5-5", llm_effort="low"))
            self.assertEqual(llm.respond(["stable", "per user"], [{"role": "user", "content": "hi"}], []).text, "hello")
        path, headers, body = api.requests[0]
        self.assertTrue(path.startswith("/v1/messages"))
        self.assertIn("server-side-fallback-2026-07-01", headers.get("anthropic-beta", ""))
        self.assertEqual(body["fallbacks"], "default")
        self.assertEqual(body["model"], "claude-opus-5-5")
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertEqual(body["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("cache_control", body["system"][1])

    def test_azure_sends_api_key_header_and_no_fallbacks(self):
        api = self.fake(message("hola"))
        llm = ClaudeLLM(self.config(llm_provider="azure", llm_model="my-deployment",
                                    azure_endpoint=api.url, azure_api_key="secret"))
        self.assertEqual(llm.respond(["s"], [{"role": "user", "content": "hi"}], []).text, "hola")
        _, headers, body = api.requests[0]
        self.assertEqual(headers["api-key"], "secret")
        self.assertNotIn("fallbacks", body)
        self.assertEqual(body["model"], "my-deployment")

    def test_refusal_becomes_an_error(self):
        api = self.fake(message("", stop_reason="refusal"))
        llm = ClaudeLLM(self.config(llm_provider="azure", llm_model="m", azure_endpoint=api.url, azure_api_key="k"))
        with self.assertRaises(LLMError):
            llm.respond(["s"], [{"role": "user", "content": "hi"}], [])

    def test_attachments_shape(self):
        from agent.llm import to_claude, to_openai
        msgs = [{"role": "user", "content": "", "attachments": [
            {"kind": "image", "name": "a.png", "mime": "image/png", "text": "", "data": "QUJD"},
            {"kind": "pdf", "name": "r.pdf", "mime": "application/pdf", "text": "report text", "data": "UERG"},
            {"kind": "audio", "name": "n.ogg", "mime": "audio/ogg", "text": "hello there"},
        ]}]
        claude = to_claude(msgs)[0]["content"]
        self.assertEqual([b["type"] for b in claude], ["image", "document", "text", "text"])
        self.assertIn("hello there", claude[2]["text"])
        self.assertEqual(claude[3]["text"], "(no text, see the attachment)")
        openai = to_openai(msgs)[0]["content"]
        self.assertEqual(openai[0]["image_url"]["url"], "data:image/png;base64,QUJD")
        self.assertIn("report text", openai[1]["text"])

    def test_tools_round_trip_with_claude(self):
        response = message("")
        response["content"] = [{"type": "thinking", "thinking": "", "signature": "sig"},
                               {"type": "tool_use", "id": "tu_1", "name": "capabilities", "input": {}}]
        response["stop_reason"] = "tool_use"
        api = self.fake(response)
        llm = ClaudeLLM(self.config(llm_provider="azure", llm_model="m", azure_endpoint=api.url, azure_api_key="k"))
        tool = Tool("capabilities", "List capabilities.", obj({}), "read", run=lambda c, a: "x")
        turn = llm.respond(["s"], [{"role": "user", "content": "what can you do?"}], [tool])
        self.assertEqual([(c.id, c.name, c.input) for c in turn.tool_calls], [("tu_1", "capabilities", {})])
        sent = api.requests[0][2]
        self.assertEqual(sent["tools"], [{"name": "capabilities", "description": "List capabilities.",
                                          "input_schema": obj({})}])
        # The next request replays the model's turn unchanged (thinking included) and the result.
        llm.respond(["s"], [
            {"role": "user", "content": "what can you do?"},
            {"role": "assistant", "content": "", "raw": turn.raw, "tool_calls": [{"id": "tu_1", "name": "capabilities", "input": {}}]},
            {"role": "tool_results", "results": [{"id": "tu_1", "name": "capabilities", "content": "lots", "is_error": False}]},
        ], [tool])
        replay = api.requests[1][2]["messages"]
        self.assertEqual(replay[1]["content"][0]["signature"], "sig")
        self.assertEqual(replay[2]["content"][0], {"type": "tool_result", "tool_use_id": "tu_1", "content": "lots", "is_error": False})

    def test_act_tools_get_a_replaces_field(self):
        tool = Tool("x_publish", "Publish.", obj({"text": {"type": "string"}}), "act", run=lambda c, a: "x",
                    card=lambda c, a: None)
        schema = tool.schema_for_model()
        self.assertIn("replaces", schema["properties"])
        self.assertEqual(schema["required"], ["text"])

    def test_openai_tool_calls(self):
        api = self.fake({"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "capabilities", "arguments": "{}"}}]}}]})
        llm = OpenAICompatibleLLM(self.config(llm_provider="openai", llm_model="gpt-x", openai_base_url=api.url))
        tool = Tool("capabilities", "List.", obj({}), "read", run=lambda c, a: "x")
        turn = llm.respond(["s"], [{"role": "user", "content": "hi"},
                                   {"role": "assistant", "content": "", "tool_calls": [{"id": "c0", "name": "capabilities", "input": {}}]},
                                   {"role": "tool_results", "results": [{"id": "c0", "name": "capabilities", "content": "r", "is_error": False}]}],
                           [tool])
        self.assertEqual(turn.tool_calls[0].name, "capabilities")
        body = api.requests[0][2]
        self.assertEqual(body["tools"][0]["function"]["name"], "capabilities")
        self.assertEqual(body["messages"][3], {"role": "tool", "tool_call_id": "c0", "content": "r"})

    def test_openai_rate_limit_is_a_usage_limit(self):
        import urllib.error
        llm = OpenAICompatibleLLM(self.config(llm_provider="openai", llm_model="m", openai_base_url="http://127.0.0.1:9"))
        error = urllib.error.HTTPError(llm.url, 429, "Too Many Requests", {}, None)
        error.read = lambda *a: b"quota exceeded"
        with mock.patch("urllib.request.urlopen", side_effect=error), self.assertRaises(UsageLimitError):
            llm.respond(["s"], [{"role": "user", "content": "hi"}], [])

    def test_openai_compatible(self):
        api = self.fake({"choices": [{"message": {"content": "yo"}}]})
        llm = OpenAICompatibleLLM(self.config(llm_provider="openai", llm_model="gpt-x",
                                              openai_base_url=api.url, openai_api_key="k"))
        self.assertEqual(llm.respond(["a", "b"], [{"role": "user", "content": "hi"}], []).text, "yo")
        path, headers, body = api.requests[0]
        self.assertEqual(path, "/chat/completions")
        self.assertEqual(headers["authorization"], "Bearer k")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "a\n\nb"})


if __name__ == "__main__":
    unittest.main()
