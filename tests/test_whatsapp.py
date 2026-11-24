"""End to end: a signed webhook call -> the agent -> replies sent to a fake Graph API."""
import hashlib
import hmac
import json
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent.channels.whatsapp import WhatsAppChannel, chunk, valid_signature
from agent.config import load_config

from tests.helpers import make_agent

SECRET = "app-secret"
# 1x1 transparent PNG
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
OWNER = "34600111222"


class FakeGraph:
    """Stands in for graph.facebook.com and records every call."""

    def __init__(self):
        self.sent: list[dict] = []
        self.statuses: list[dict] = []
        self.uploads: list[bytes] = []
        graph = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def _json(self, data):
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/file/media-1":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"OGGDATA")
                    return
                if self.path == "/file/img-1":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(PNG)
                    return
                if self.path.endswith("/img-1"):
                    return self._json({"url": f"http://127.0.0.1:{graph.port}/file/img-1", "mime_type": "image/png"})
                if self.path.endswith("/big-1"):
                    return self._json({"url": f"http://127.0.0.1:{graph.port}/file/big-1", "mime_type": "video/mp4", "file_size": 999_000_000})
                if self.path.endswith("/media-1"):
                    return self._json({"url": f"http://127.0.0.1:{graph.port}/file/media-1", "mime_type": "audio/ogg; codecs=opus"})
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                assert self.headers["Authorization"] == "Bearer token"
                if self.path.endswith("/media"):
                    graph.uploads.append(raw)
                    return self._json({"id": "uploaded-1"})
                payload = json.loads(raw)
                (graph.statuses if "status" in payload else graph.sent).append(payload)
                self._json({"messages": [{"id": "out"}]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def payload(message: dict) -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {"messages": [message]}}]}]}


class WhatsAppBase(unittest.TestCase):
    def setUp(self):
        self.graph = FakeGraph()
        self.addCleanup(self.graph.server.server_close)
        self.addCleanup(self.graph.server.shutdown)
        self.agent, self.llm, tmp = make_agent(
            "[SETUP: Ana | @ana | CRM]Hi Ana", "Two drafts:\n```\nPost one\n```\n```\nPost two\n```\nGo with one."
        )
        self.addCleanup(tmp.cleanup)
        config = replace(
            load_config(env_file=Path(tmp.name) / "no.env"),
            whatsapp_token="token", whatsapp_phone_number_id="999", whatsapp_app_secret=SECRET,
            whatsapp_verify_token="verify", whatsapp_allowed_numbers={OWNER},
            whatsapp_graph_url=f"http://127.0.0.1:{self.graph.port}", language="en",
        )
        self.heard: list[tuple[bytes, str]] = []

        def transcriber(audio, suffix):
            self.heard.append((audio, suffix))
            return "make a tweet about shipping"

        self.channel = WhatsAppChannel(config, self.agent, transcriber)
        self.server = self.channel.make_server(port=0)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, body: dict, secret: str = SECRET) -> int:
        raw = json.dumps(body).encode()
        sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        req = urllib.request.Request(f"{self.url}/webhook", data=raw,
                                     headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            e.close()
            return e.code

    def wait(self):
        self.channel.drain()

    def bodies(self):
        return [m["text"]["body"] for m in self.graph.sent if m["type"] == "text"]


class WhatsAppTest(WhatsAppBase):
    def test_verification_handshake(self):
        with urllib.request.urlopen(f"{self.url}/webhook?hub.mode=subscribe&hub.verify_token=verify&hub.challenge=42") as ok:
            self.assertEqual(ok.read(), b"42")
        with self.assertRaises(urllib.error.HTTPError) as bad:
            urllib.request.urlopen(f"{self.url}/webhook?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=42")
        self.assertEqual(bad.exception.code, 403)
        bad.exception.close()

    def test_conversation_with_text_and_voice(self):
        self.assertEqual(self.post(payload({"from": OWNER, "id": "m1", "type": "text", "text": {"body": "hola"}})), 200)
        self.assertEqual(self.post(payload({"from": OWNER, "id": "m2", "type": "text", "text": {"body": "I'm Ana"}})), 200)
        self.assertEqual(self.post(payload({"from": OWNER, "id": "m3", "type": "audio", "audio": {"id": "media-1"}})), 200)
        self.wait()
        bodies = self.bodies()
        self.assertIn("TestBot", bodies[0])
        self.assertEqual(bodies[1], "Hi Ana")
        # Each draft arrives as its own message, without backticks.
        self.assertEqual(bodies[2:], ["Two drafts:", "Post one", "Post two", "Go with one."])
        self.assertEqual(self.heard, [(b"OGGDATA", ".ogg")])
        voice = self.llm.calls[-1][1][-1]["attachments"][0]
        self.assertEqual((voice["kind"], voice["text"]), ("audio", "make a tweet about shipping"))
        self.assertTrue(all(m["to"] == OWNER for m in self.graph.sent))
        self.assertEqual({s["message_id"] for s in self.graph.statuses}, {"m1", "m2", "m3"})

    def test_bad_signature_is_rejected(self):
        self.assertEqual(self.post(payload({"from": OWNER, "id": "m1", "type": "text", "text": {"body": "hi"}}), secret="wrong"), 401)
        self.wait()
        self.assertEqual(self.graph.sent, [])

    def test_strangers_and_duplicates_are_ignored(self):
        self.post(payload({"from": "34999999999", "id": "s1", "type": "text", "text": {"body": "hi"}}))
        self.post(payload({"from": OWNER, "id": "m1", "type": "text", "text": {"body": "hi"}}))
        self.post(payload({"from": OWNER, "id": "m1", "type": "text", "text": {"body": "hi"}}))
        self.wait()
        self.assertEqual(len(self.graph.sent), 1)

    def test_unsupported_message_type(self):
        self.post(payload({"from": OWNER, "id": "l1", "type": "location", "location": {"latitude": 1}}))
        self.post(payload({"from": OWNER, "id": "r1", "type": "reaction", "reaction": {"emoji": "x"}}))
        self.wait()
        self.assertEqual(len(self.bodies()), 1)
        self.assertIn("can't read", self.bodies()[0])


class WhatsAppFilesTest(WhatsAppBase):
    """Photos in, documents out."""

    def setUp(self):
        super().setUp()
        self.llm.replies = ["[SETUP: Ana | @ana | CRM]Hi", "Here is your plan.\n[FILE: plan.pdf]\n# Plan\n- Monday: post\n[/FILE]"]
        self.post(payload({"from": OWNER, "id": "a1", "type": "text", "text": {"body": "hola"}}))
        self.post(payload({"from": OWNER, "id": "a2", "type": "text", "text": {"body": "I'm Ana"}}))
        self.wait()

    def test_photo_in_pdf_out(self):
        self.post(payload({"from": OWNER, "id": "p1", "type": "image",
                           "image": {"id": "img-1", "mime_type": "image/png", "caption": "make a plan from this"}}))
        self.wait()
        sent = self.llm.calls[-1][1][-1]
        self.assertEqual(sent["content"], "make a plan from this")
        self.assertEqual(sent["attachments"][0]["kind"], "image")
        self.assertTrue(sent["attachments"][0]["data"])  # the image itself reaches the model
        self.assertEqual(self.bodies()[-1], "Here is your plan.")
        document = self.graph.sent[-1]
        self.assertEqual(document["type"], "document")
        self.assertEqual(document["document"], {"id": "uploaded-1", "filename": "plan.pdf"})
        self.assertIn(b"%PDF", self.graph.uploads[0])

    def test_file_too_big_is_refused(self):
        self.post(payload({"from": OWNER, "id": "v1", "type": "video", "video": {"id": "big-1"}}))
        self.wait()
        self.assertIn("too big", self.bodies()[-1])


class HelpersTest(unittest.TestCase):
    def test_signature(self):
        body = b'{"a":1}'
        good = "sha256=" + hmac.new(b"s", body, hashlib.sha256).hexdigest()
        self.assertTrue(valid_signature("s", body, good))
        self.assertFalse(valid_signature("s", body, "sha256=00"))
        self.assertFalse(valid_signature("", body, good))

    def test_chunk_long_text(self):
        parts = chunk("line\n" * 2000, limit=100)
        self.assertTrue(all(len(p) <= 100 for p in parts))
        self.assertEqual("".join(p.replace("\n", "") for p in parts), "line" * 2000)


if __name__ == "__main__":
    unittest.main()
