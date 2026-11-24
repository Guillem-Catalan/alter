"""Model providers behind one interface (ADR 0001): `respond(system_blocks, messages, tools) -> Turn`.

`system_blocks` is a list of strings: the first one is stable across turns (cached where the provider supports
it), the rest change per user and per turn.

Messages use a provider-neutral shape:
- {"role": "user", "content": str, "attachments"?: [...]}: each attachment has kind, name, mime, text and,
  for images and PDFs the model should see as they are, base64 `data`.
- {"role": "assistant", "content": str, "tool_calls"?: [{"id", "name", "input"}], "raw"?: provider content}
- {"role": "tool_results", "results": [{"id", "name", "content", "is_error"}]}
Adapters turn them into each API's format. `raw` keeps the provider's own content blocks (thinking included)
so a turn with tool calls is sent back exactly as the model produced it.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .config import Config
from .tools import Tool, ToolCall

# Models that accept the server-side refusal fallback on the first-party Claude API.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1", "claude-opus-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000


class LLMError(RuntimeError):
    pass


class UsageLimitError(LLMError):
    """The provider's plan or quota is used up. `message` is the provider's own words, safe to show the user."""


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: list | None = None


def _attachment_note(a: dict) -> str:
    body = a.get("text") or "(the file itself is no longer attached; ask the user to send it again if you need it)"
    label = {"audio": "voice note transcript", "video": "video transcript"}.get(a["kind"], a["kind"])
    return f'<attachment name="{a["name"]}" type="{label}">\n{body}\n</attachment>'


def _text_of(message: dict) -> str:
    return message["content"] or "(no text, see the attachment)"


def to_claude(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        role = m["role"]
        if role == "tool_results":
            out.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"], "is_error": r.get("is_error", False)}
                for r in m["results"]]})
        elif role == "assistant":
            if m.get("raw"):
                out.append({"role": "assistant", "content": m["raw"]})
            elif m.get("tool_calls"):
                blocks: list[dict] = [{"type": "text", "text": m["content"]}] if m["content"] else []
                blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]} for c in m["tool_calls"]]
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": "assistant", "content": m["content"]})
        elif not m.get("attachments"):
            out.append({"role": role, "content": m["content"]})
        else:
            parts: list[dict] = []
            for a in m["attachments"]:
                if a.get("data") and a["kind"] == "image":
                    parts.append({"type": "image", "source": {"type": "base64", "media_type": a["mime"], "data": a["data"]}})
                elif a.get("data") and a["kind"] == "pdf":
                    parts.append({"type": "document", "title": a["name"],
                                  "source": {"type": "base64", "media_type": "application/pdf", "data": a["data"]}})
                else:
                    parts.append({"type": "text", "text": _attachment_note(a)})
            parts.append({"type": "text", "text": _text_of(m)})
            out.append({"role": role, "content": parts})
    return out


def to_openai(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        role = m["role"]
        if role == "tool_results":
            out += [{"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in m["results"]]
        elif role == "assistant":
            message: dict = {"role": "assistant", "content": m["content"] or None}
            if m.get("tool_calls"):
                message["tool_calls"] = [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["input"])}}
                    for c in m["tool_calls"]]
            out.append(message)
        elif not m.get("attachments"):
            out.append({"role": role, "content": m["content"]})
        else:
            parts: list[dict] = []
            for a in m["attachments"]:
                if a.get("data") and a["kind"] == "image":
                    parts.append({"type": "image_url", "image_url": {"url": f"data:{a['mime']};base64,{a['data']}"}})
                else:  # PDFs go as their extracted text: not every OpenAI-compatible API reads files
                    parts.append({"type": "text", "text": _attachment_note(a)})
            parts.append({"type": "text", "text": _text_of(m)})
            out.append({"role": role, "content": parts})
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

    def respond(self, system_blocks: list[str], messages: list[dict], tools: list[Tool]) -> Turn:
        system: list[dict] = [{"type": "text", "text": block} for block in system_blocks if block]
        if system:
            system[0]["cache_control"] = {"type": "ephemeral"}
        params: dict = {"model": self.model, "max_tokens": MAX_TOKENS, "system": system, "messages": to_claude(messages)}
        if tools:
            params["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.schema_for_model()}
                               for t in tools]
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        try:
            if self.fallbacks:
                response = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
            else:
                response = self.client.messages.create(**params)
        except self._anthropic.RateLimitError as e:
            raise UsageLimitError(f"Claude API rate limit: {e.message}") from e
        except self._anthropic.APIStatusError as e:
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise LLMError(f"Could not reach the Claude API: {e}") from e
        if response.stop_reason == "refusal":
            raise LLMError("The model declined this request.")
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"]
        raw = response.to_dict()["content"] if calls else None
        return Turn(text, calls, raw)


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

    def respond(self, system_blocks: list[str], messages: list[dict], tools: list[Tool]) -> Turn:
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": "\n\n".join(b for b in system_blocks if b)}] + to_openai(messages),
        }
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.schema_for_model()}} for t in tools]
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:  # nosemgrep: dynamic-urllib-use-detected (scheme checked in __init__)
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read()[:300]
            if e.code == 429:
                raise UsageLimitError(f"Model API rate limit: {detail!r}") from e
            raise LLMError(f"Model API error {e.code}: {detail!r}") from e
        except urllib.error.URLError as e:
            raise LLMError(f"Could not reach the model API: {e.reason}") from e
        message = data["choices"][0]["message"]
        calls = []
        for c in message.get("tool_calls") or []:
            try:
                args = json.loads(c["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_json": c["function"]["arguments"]}
            calls.append(ToolCall(c["id"], c["function"]["name"], args if isinstance(args, dict) else {}))
        return Turn(message.get("content") or "", calls)


def make_llm(config: Config):
    if config.llm_provider in ("anthropic", "azure"):
        return ClaudeLLM(config)
    if config.llm_provider == "openai":
        return OpenAICompatibleLLM(config)
    raise LLMError(f"Unknown LLM_PROVIDER {config.llm_provider!r} (use anthropic, azure or openai)")
