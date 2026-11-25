# Alter

Self-hosted publishing assistant that lives in WhatsApp: it takes text, voice notes, photos, documents and
videos, and returns drafts for X, LinkedIn, Instagram and YouTube in the user's voice, plus files (PDF, Word,
CSV). Open source: anyone clones it and runs it with their own keys. User-facing docs: `README.md`.

## Stack
- Python 3.10+, stdlib + `anthropic` (official SDK), `fpdf2` and `python-docx` (create files), `pypdf` and
  `defusedxml` (read them). `slack-bolt` only for the optional Slack channel.
- Model: `LLM_PROVIDER` = `anthropic` (default, `claude-opus-5-5` with server-side refusal fallback), `azure`
  (Anthropic-compatible endpoint), `openai` (any OpenAI-compatible API) or `claude-code` (the user's own signed-in
  Claude Code via `claude -p` and structured output; never touch its credentials). `LLM_FALLBACK_PROVIDER` answers
  when the main one hits its usage limit.
- Voice: whisper.cpp (`whisper-cli`) + ffmpeg, locally. WhatsApp: Meta Cloud API webhook + a tunnel (ngrok).

## Layout
- `agent/brain.py`: the agent loop shared by every channel: instructions, tool calls, commands, and card
  confirm/cancel from the user's own message (never from the model).
- `agent/tools.py` (Tool, kinds internal/read/act, schema validation), `agent/core_tools.py` (skills, files,
  memory, capabilities), `agent/actions.py` (pending actions, cards, audit log), `agent/db.py` (SQLite, named
  migrations), `agent/secrets.py` (OS keychain), `agent/connectors/` (Connector, capabilities, connections).
- `agent/skills.py`: finds `skills/**/SKILL.md`; `load_skill` returns the body plus data files (scripts never run).
  `li-human`'s `humanize.py` cleans drafts.
- `agent/files.py` (incoming files to text or inline images/PDFs), `agent/documents.py` (Markdown to PDF/DOCX/CSV),
  `agent/llm.py` (provider formats), `agent/transcribe.py`, `agent/store.py`, `agent/config.py`.
- `agent/channels/`: `whatsapp.py` (webhook, signature, per-sender queue, media in and out), `slack.py`, `terminal.py`.
- `skills/linkedin`, `skills/instagram`, `skills/youtube`: vendored from Jake Schincariol's MIT repos (keep their
  `LICENSE`). `skills/x/x-post` is ours.

## Commands
```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
scripts/download-whisper-model.sh
.venv/bin/python main.py doctor
.venv/bin/python main.py chat [-m "..."] [-f file]
.venv/bin/python main.py whatsapp
.venv/bin/python -m unittest discover -s tests -t .
```

## Rules
- Personal data never goes in the repo: it lives in the per-user data folder (`default_data_dir`), created
  with owner-only permissions. Never add fixtures, examples or logs with real people's data.
- Never read, print or commit `.env`. New settings: add the name to `.env.example` and the README.
- Act tools never run when the model calls them: only `Actions.confirm`, reached from a user's button or typed
  yes. Never add a tool that confirms or cancels cards. Keep `tests/test_actions.py` passing.
- Untrusted text is fenced by `wrap_untrusted` (random boundary); tools with `remembers=True` are refused while
  untrusted text is in the history (`untrusted` flag on the reply; `new chat` clears it). New memory-changing tools
  must set `remembers=True`. Stored text going into the system prompt passes through `neutralise`.
- New tables go in `agent/db.py` `MIGRATIONS` with a unique name; never edit a migration that has shipped.
- Connector capabilities must be honest; acting only through official APIs (ADR 0006).
- Alter never publishes: it returns text and files. No invented metrics.
- Only `WHATSAPP_ALLOWED_NUMBERS` get answers; every webhook call is checked against `WHATSAPP_APP_SECRET`.
- Code, comments and docs in English; the bot chats in the user's language (`BOT_LANGUAGE` for its fixed texts).

## Gotchas
- The webhook must answer 200 fast: work goes to a per-sender queue (keeps order); Meta retries, messages are
  deduplicated by id.
- Meta media URLs expire in 5 minutes and need the token: download right away.
- No scheduled messages: outside WhatsApp's 24 h window Meta requires paid templates.
- Only the last 3 messages with images or PDFs go inline (`INLINE_ATTACHMENT_TURNS`); older ones as text.
- PDFs cannot draw emojis; the model is told so.
- On macOS the Mac must not sleep for the bot to answer (`sudo pmset -c sleep 0`).

## Verification
- `.venv/bin/python -m unittest discover -s tests -t .` passes (fake model and fake WhatsApp API, no keys needed).
- `.venv/bin/python main.py doctor` shows no `FIX`.
- Real check: a text and a voice note over WhatsApp get drafts, each in its own message.
