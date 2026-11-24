"""The real SDK against a local fake of the Messages API: checks what we send and how we read the answer."""
import json
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from agent.config import load_config
from agent.llm import ClaudeLLM, LLMError, OpenAICompatibleLLM


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
            self.assertEqual(llm.complete(["stable", "per user"], [{"role": "user", "content": "hi"}]), "hello")
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
        self.assertEqual(llm.complete(["s"], [{"role": "user", "content": "hi"}]), "hola")
        _, headers, body = api.requests[0]
        self.assertEqual(headers["api-key"], "secret")
        self.assertNotIn("fallbacks", body)
        self.assertEqual(body["model"], "my-deployment")

    def test_refusal_becomes_an_error(self):
        api = self.fake(message("", stop_reason="refusal"))
        llm = ClaudeLLM(self.config(llm_provider="azure", llm_model="m", azure_endpoint=api.url, azure_api_key="k"))
        with self.assertRaises(LLMError):
            llm.complete(["s"], [{"role": "user", "content": "hi"}])

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

    def test_openai_compatible(self):
        api = self.fake({"choices": [{"message": {"content": "yo"}}]})
        llm = OpenAICompatibleLLM(self.config(llm_provider="openai", llm_model="gpt-x",
                                              openai_base_url=api.url, openai_api_key="k"))
        self.assertEqual(llm.complete(["a", "b"], [{"role": "user", "content": "hi"}]), "yo")
        path, headers, body = api.requests[0]
        self.assertEqual(path, "/chat/completions")
        self.assertEqual(headers["authorization"], "Bearer k")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "a\n\nb"})


if __name__ == "__main__":
    unittest.main()
