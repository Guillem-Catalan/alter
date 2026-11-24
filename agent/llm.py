"""Model providers behind one small interface: `complete(system_blocks, messages) -> str`.

`system_blocks` is a list of strings: the first one is stable across turns (cached where the
provider supports it), the rest change per user and per turn.

Messages are {"role", "content", "attachments"?}. Each attachment has kind, name, mime, text and,
for images and PDFs the model should see as they are, base64 `data`.
"""
import json
import urllib.error
import urllib.request

from .config import Config

# Models that accept the server-side refusal fallback on the first-party Claude API.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1", "claude-opus-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000


class LLMError(RuntimeError):
    pass


def _attachment_note(a: dict) -> str:
    body = a.get("text") or "(the file itself is no longer attached; ask the user to send it again if you need it)"
    label = {"audio": "voice note transcript", "video": "video transcript"}.get(a["kind"], a["kind"])
    return f'<attachment name="{a["name"]}" type="{label}">\n{body}\n</attachment>'


def _text_of(message: dict) -> str:
    return message["content"] or "(no text, see the attachment)"


def to_claude(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if not m.get("attachments"):
            out.append({"role": m["role"], "content": m["content"]})
            continue
        blocks: list[dict] = []
        for a in m["attachments"]:
            if a.get("data") and a["kind"] == "image":
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": a["mime"], "data": a["data"]}})
            elif a.get("data") and a["kind"] == "pdf":
                blocks.append({"type": "document", "title": a["name"],
                               "source": {"type": "base64", "media_type": "application/pdf", "data": a["data"]}})
            else:
                blocks.append({"type": "text", "text": _attachment_note(a)})
        blocks.append({"type": "text", "text": _text_of(m)})
        out.append({"role": m["role"], "content": blocks})
    return out


def to_openai(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if not m.get("attachments"):
            out.append({"role": m["role"], "content": m["content"]})
            continue
        parts: list[dict] = []
        for a in m["attachments"]:
            if a.get("data") and a["kind"] == "image":
                parts.append({"type": "image_url", "image_url": {"url": f"data:{a['mime']};base64,{a['data']}"}})
            else:  # PDFs go as their extracted text: not every OpenAI-compatible API reads files
                parts.append({"type": "text", "text": _attachment_note(a)})
        parts.append({"type": "text", "text": _text_of(m)})
        out.append({"role": m["role"], "content": parts})
    return out


class ClaudeLLM:
    """Claude through the official SDK: the Anthropic API, or an Anthropic-compatible Azure endpoint."""

    def __init__(self, config: Config):
        import anthropic

        self._anthropic = anthropic
        self.model = config.llm_model
        self.effort = config.llm_effort
        if config.llm_provider == "azure":
            if not (config.azure_endpoint and config.azure_api_key):
                raise LLMError("LLM_PROVIDER=azure needs AZURE_CLAUDE_ENDPOINT and AZURE_CLAUDE_API_KEY")
            self.client = anthropic.Anthropic(
                base_url=config.azure_endpoint,
                api_key=config.azure_api_key,
                default_headers={"api-key": config.azure_api_key},
            )
            self.fallbacks = False
        else:
            self.client = anthropic.Anthropic()
            self.fallbacks = config.llm_fallbacks and self.model in FALLBACK_MODELS

    def complete(self, system_blocks: list[str], messages: list[dict]) -> str:
        system: list[dict] = [{"type": "text", "text": block} for block in system_blocks if block]
        if system:
            system[0]["cache_control"] = {"type": "ephemeral"}
        params = {"model": self.model, "max_tokens": MAX_TOKENS, "system": system, "messages": to_claude(messages)}
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        try:
            if self.fallbacks:
                response = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
            else:
                response = self.client.messages.create(**params)
        except self._anthropic.APIStatusError as e:
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise LLMError(f"Could not reach the Claude API: {e}") from e
        if response.stop_reason == "refusal":
            raise LLMError("The model declined this request.")
        return "".join(block.text for block in response.content if block.type == "text")


class OpenAICompatibleLLM:
    """Any OpenAI-compatible chat completions endpoint (OpenAI, OpenRouter, Ollama, Gemini, Groq...)."""

    def __init__(self, config: Config):
        if not config.llm_model:
            raise LLMError("LLM_PROVIDER=openai needs LLM_MODEL")
        if not config.openai_base_url.startswith(("https://", "http://")):
            raise LLMError("OPENAI_BASE_URL must start with https:// or http://")
        self.url = f"{config.openai_base_url}/chat/completions"
        self.key = config.openai_api_key
        self.model = config.llm_model

    def complete(self, system_blocks: list[str], messages: list[dict]) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": "\n\n".join(b for b in system_blocks if b)}] + to_openai(messages),
        }
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:  # nosemgrep: dynamic-urllib-use-detected (scheme checked in __init__)
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise LLMError(f"Model API error {e.code}: {e.read()[:300]!r}") from e
        except urllib.error.URLError as e:
            raise LLMError(f"Could not reach the model API: {e.reason}") from e
        return data["choices"][0]["message"]["content"] or ""


def make_llm(config: Config):
    if config.llm_provider in ("anthropic", "azure"):
        return ClaudeLLM(config)
    if config.llm_provider == "openai":
        return OpenAICompatibleLLM(config)
    raise LLMError(f"Unknown LLM_PROVIDER {config.llm_provider!r} (use anthropic, azure or openai)")
