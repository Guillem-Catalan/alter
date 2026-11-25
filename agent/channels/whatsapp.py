"""WhatsApp channel through Meta's official WhatsApp Cloud API.

Meta calls a webhook on this machine for every incoming message (exposed with a tunnel such
as ngrok). The server answers 200 at once and handles the message in the background.
"""
import hashlib
import hmac
import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..brain import Agent, split_drafts
from ..config import Config, digits
from ..files import MAX_FILE_BYTES, UnsupportedFile, ingest
from ..store import UserStore

log = logging.getLogger("whatsapp")

MAX_BODY_BYTES = 1_000_000
MAX_TEXT_CHARS = 4000  # WhatsApp's limit is 4096
MEDIA_TYPES = ("image", "document", "audio", "video", "sticker")
IGNORED_TYPES = ("reaction", "system", "unsupported_ignored")
MAX_BUBBLES = 4
# Reacting straight away shows the user their file arrived while it is still being read.
REACTIONS = {"audio": "🎧", "image": "👀", "video": "👀", "document": "📄"}
# WhatsApp only accepts some document types; everything we generate is sent as one of these.
OUTGOING_MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

TEXTS = {
    "en": {
        "confirm": "Confirm",
        "cancel": "Cancel",
        "ttl": "Confirm or cancel within 24 h",
        "unsupported": "I can't read that kind of message. Send me text, voice notes, photos, documents or videos.",
        "file_failed": "I couldn't read that file: {error}.",
        "audio_empty": "I couldn't hear anything in that voice note. Could you try again?",
    },
    "es": {
        "confirm": "Confirmar",
        "cancel": "Cancelar",
        "ttl": "Confirma o cancela en 24 h",
        "unsupported": "Ese tipo de mensaje no lo puedo leer. Mándame texto, audios, fotos, documentos o vídeos.",
        "file_failed": "No he podido leer ese archivo: {error}.",
        "audio_empty": "No he oído nada en ese audio. ¿Lo intentas otra vez?",
    },
}


def valid_signature(app_secret: str, body: bytes, header: str) -> bool:
    if not app_secret or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def chunk(text: str, limit: int = MAX_TEXT_CHARS) -> list[str]:
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


class WhatsAppClient:
    def __init__(self, config: Config):
        self.base = f"{config.whatsapp_graph_url}/{config.whatsapp_api_version}"
        self.token = config.whatsapp_token
        self.phone_number_id = config.whatsapp_phone_number_id

    def _request(self, url: str, payload: dict | None = None) -> bytes:
        if urllib.parse.urlparse(url).scheme not in ("https", "http"):
            raise ValueError(f"Refusing a non-HTTP URL: {url[:40]}")
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Authorization": f"Bearer {self.token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosemgrep: dynamic-urllib-use-detected (scheme checked above)
            return resp.read()

    def send_text(self, to: str, text: str) -> None:
        for part in chunk(text):
            self._request(f"{self.base}/{self.phone_number_id}/messages", {
                "messaging_product": "whatsapp", "recipient_type": "individual",
                "to": to, "type": "text", "text": {"body": part, "preview_url": False},
            })

    def mark_read_and_typing(self, message_id: str) -> None:
        self._request(f"{self.base}/{self.phone_number_id}/messages", {
            "messaging_product": "whatsapp", "status": "read",
            "message_id": message_id, "typing_indicator": {"type": "text"},
        })

    def react(self, to: str, message_id: str, emoji: str) -> None:
        self._request(f"{self.base}/{self.phone_number_id}/messages", {
            "messaging_product": "whatsapp", "recipient_type": "individual",
            "to": to, "type": "reaction", "reaction": {"message_id": message_id, "emoji": emoji},
        })

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        meta = json.loads(self._request(f"{self.base}/{urllib.parse.quote(media_id)}"))
        if int(meta.get("file_size") or 0) > MAX_FILE_BYTES:
            raise UnsupportedFile(f"too big ({int(meta['file_size']) // 1_000_000} MB, limit {MAX_FILE_BYTES // 1_000_000} MB)")
        return self._request(meta["url"]), meta.get("mime_type", "")

    def upload_media(self, path: Path, mime: str) -> str:
        boundary = uuid.uuid4().hex
        fields = [("messaging_product", "whatsapp"), ("type", mime)]
        body = b"".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields
        ) + (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"{self.base}/{self.phone_number_id}/media", data=body,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        if urllib.parse.urlparse(req.full_url).scheme not in ("https", "http"):
            raise ValueError("Refusing a non-HTTP URL")
        with urllib.request.urlopen(req, timeout=120) as resp:  # nosemgrep: dynamic-urllib-use-detected (scheme checked above)
            return json.loads(resp.read())["id"]

    def send_document(self, to: str, path: Path) -> None:
        media_id = self.upload_media(path, OUTGOING_MIME.get(path.suffix.lower(), "text/plain"))
        self._request(f"{self.base}/{self.phone_number_id}/messages", {
            "messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
            "type": "document", "document": {"id": media_id, "filename": path.name},
        })

    def send_card(self, to: str, card_id: str, summary: str, content: str, image: str, texts: dict) -> None:
        """The exact content first (so it can be read and copied in full), then the card with its buttons."""
        if content:
            self.send_text(to, content)
        interactive: dict = {
            "type": "button",
            "body": {"text": summary[:1024]},
            "footer": {"text": texts["ttl"][:60]},
            "action": {"buttons": [
                {"type": "reply", "reply": {"id": f"confirm:{card_id}", "title": texts["confirm"][:20]}},
                {"type": "reply", "reply": {"id": f"cancel:{card_id}", "title": texts["cancel"][:20]}},
            ]},
        }
        if image and Path(image).exists():
            interactive["header"] = {"type": "image", "image": {"id": self.upload_media(Path(image), "image/png")}}
        self._request(f"{self.base}/{self.phone_number_id}/messages", {
            "messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
            "type": "interactive", "interactive": interactive,
        })


class WhatsAppChannel:
    def __init__(self, config: Config, agent: Agent, transcriber, client: WhatsAppClient | None = None):
        self.config = config
        self.agent = agent
        self.transcriber = transcriber
        self.client = client or WhatsAppClient(config)
        self.texts = TEXTS.get(config.language, TEXTS["en"])
        # One worker per sender: each person's messages are answered in the order they were sent.
        # Senders are limited to WHATSAPP_ALLOWED_NUMBERS, so this stays small.
        self._queues: dict[str, ThreadPoolExecutor] = {}
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._seen_lock = threading.Lock()

    def _queue_for(self, sender: str) -> ThreadPoolExecutor:
        with self._seen_lock:
            if sender not in self._queues:
                self._queues[sender] = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"wa-{sender[-4:]}")
            return self._queues[sender]

    def drain(self) -> None:
        """Wait until every queued message has been answered."""
        with self._seen_lock:
            queues = list(self._queues.values())
        for queue in queues:
            queue.submit(lambda: None).result()

    def missing_settings(self) -> list[str]:
        names = {
            "WHATSAPP_TOKEN": self.config.whatsapp_token,
            "WHATSAPP_PHONE_NUMBER_ID": self.config.whatsapp_phone_number_id,
            "WHATSAPP_APP_SECRET": self.config.whatsapp_app_secret,
            "WHATSAPP_VERIFY_TOKEN": self.config.whatsapp_verify_token,
            "WHATSAPP_ALLOWED_NUMBERS": self.config.whatsapp_allowed_numbers,
        }
        return [name for name, value in names.items() if not value]

    def _first_time(self, message_id: str) -> bool:
        """Meta may deliver the same webhook more than once; answer each message only once."""
        with self._seen_lock:
            if message_id in self._seen:
                return False
            self._seen[message_id] = None
            while len(self._seen) > 1000:
                self._seen.popitem(last=False)
            return True

    def handle_payload(self, payload: dict) -> list:
        """Queue every new message in a webhook payload. Returns the futures (used by tests)."""
        futures = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                for message in change.get("value", {}).get("messages", []):
                    sender = digits(message.get("from", ""))
                    if sender not in self.config.whatsapp_allowed_numbers:
                        log.warning("Ignoring message from a number not in WHATSAPP_ALLOWED_NUMBERS (ends in %s)", sender[-4:])
                        continue
                    if not self._first_time(message.get("id", "")):
                        continue
                    futures.append(self._queue_for(sender).submit(self._process, sender, message))
        return futures

    def _process(self, sender: str, message: dict) -> None:
        try:
            try:
                self.client.mark_read_and_typing(message["id"])
                if message.get("type") in REACTIONS:
                    self.client.react(sender, message["id"], REACTIONS[message["type"]])
            except (urllib.error.URLError, KeyError) as e:
                log.info("Could not show the typing indicator or react: %s", e)
            uid = f"wa-{sender}"
            content = self._content_of(uid, message)
            if content is None:
                return
            text, attachments, button = content
            reply = self.agent.handle(uid, text, channel="whatsapp", attachments=attachments, button=button)
            for part in split_drafts(reply.text, bubbles=MAX_BUBBLES):
                self.client.send_text(sender, part)
            for path in reply.files:
                self.client.send_document(sender, path)
            for card in reply.cards:
                self.client.send_card(sender, card.id, card.summary, card.content, card.image, self.texts)
        except Exception:
            log.exception("Failed to answer a WhatsApp message")

    def _content_of(self, uid: str, message: dict):
        """(text, attachments, button) for a message, or None when it was answered here (unsupported, failed)."""
        kind = message.get("type")
        sender = digits(message.get("from", ""))
        if kind == "text":
            return message.get("text", {}).get("body", ""), [], ""
        if kind == "interactive":
            reply = message.get("interactive", {}).get("button_reply") or {}
            return "", [], str(reply.get("id", ""))
        if kind == "button":  # quick-reply buttons of template messages
            return "", [], str(message.get("button", {}).get("payload", ""))
        if kind in IGNORED_TYPES:
            return None
        if kind not in MEDIA_TYPES:
            self.client.send_text(sender, self.texts["unsupported"])
            return None
        media = message.get(kind, {})
        try:
            data, mime = self.client.download_media(media["id"])
            name = media.get("filename") or f"{kind}"
            attachment = ingest(data, mime or media.get("mime_type", ""), name,
                                UserStore(self.agent.data_dir, uid).dir, self.transcriber, self.config.ffmpeg_bin)
        except UnsupportedFile as e:
            self.client.send_text(sender, self.texts["file_failed"].format(error=e))
            return None
        except Exception as e:  # download, conversion or transcription
            log.warning("Could not read a %s: %s", kind, e)
            self.client.send_text(sender, self.texts["file_failed"].format(error=type(e).__name__))
            return None
        if kind == "audio" and not attachment.text.strip():
            self.client.send_text(sender, self.texts["audio_empty"])
            return None
        return media.get("caption", ""), [attachment], ""

    def make_server(self, host: str = "127.0.0.1", port: int | None = None) -> ThreadingHTTPServer:
        channel = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002 - name required by the base class
                log.debug(format, *args)

            def _reply(self, status: int, body: bytes = b"") -> None:
                self.send_response(status)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                if url.path == "/health":
                    return self._reply(200, b"ok")
                if url.path != "/webhook":
                    return self._reply(404)
                q = urllib.parse.parse_qs(url.query)
                token = q.get("hub.verify_token", [""])[0]
                if (q.get("hub.mode", [""])[0] == "subscribe" and channel.config.whatsapp_verify_token
                        and hmac.compare_digest(token, channel.config.whatsapp_verify_token)):
                    return self._reply(200, q.get("hub.challenge", [""])[0].encode())
                return self._reply(403)

            def do_POST(self):
                if urllib.parse.urlparse(self.path).path != "/webhook":
                    return self._reply(404)
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > MAX_BODY_BYTES:
                    return self._reply(413)
                body = self.rfile.read(length)
                if not valid_signature(channel.config.whatsapp_app_secret, body,
                                       self.headers.get("X-Hub-Signature-256", "")):
                    log.warning("Rejected a webhook call with a bad signature")
                    return self._reply(401)
                # Queue before answering so back-to-back webhooks keep their order; queuing is instant.
                try:
                    channel.handle_payload(json.loads(body))
                except json.JSONDecodeError:
                    log.warning("Webhook body was not JSON")
                self._reply(200, b"ok")

        return ThreadingHTTPServer((host, port if port is not None else self.config.port), Handler)
