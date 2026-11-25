# Alter

**Your alter ego for communication.** A self-hosted specialist that lives in your WhatsApp, catches how you talk
and writes in *your* voice: posts for **X, LinkedIn, Instagram and YouTube**, and emails or messages too.
Send it an idea by text or **voice note** and it turns it into something that sounds like you. It learns from
everything you tell it, and it never does anything on your behalf without your confirmation: drafts arrive one per
message, ready to copy, and every action comes as a card you confirm or cancel.

- **Runs on your machine.** Your chats, style and voice notes stay in a private folder on your computer, outside
  the repo. Voice notes are transcribed locally with [whisper.cpp](https://github.com/ggml-org/whisper.cpp).
- **Skills.** Playbooks for each job (LinkedIn posts, reel scripts, captions, carousels, X threads...) in the
  standard `SKILL.md` format. The model loads one only when it needs it. Drop in your own.
- **Any model.** Claude through the Anthropic API (default), an Anthropic-compatible Azure endpoint, or any
  OpenAI-compatible API (OpenAI, OpenRouter, Ollama, Gemini...).
- **Files in and out.** Send photos, screenshots, PDFs, Word/Excel/PowerPoint or text files and videos (their audio
  is transcribed); ask for a PDF, Word or CSV (a content plan, a report, a script) and it sends the file back.
- **Channels.** WhatsApp (official Cloud API), Slack (optional) and the terminal.

## Privacy

Alter is built so that cloning, using or forking this repo never exposes anyone's personal data:

- **Your data never lives in the repo.** Profile, learned style, voice samples, chat history, the files you send
  and the voice model go to a per-user folder: `~/Library/Application Support/Alter` on macOS,
  `~/.local/share/alter` on Linux, `%APPDATA%\Alter` on Windows (or `DATA_DIR`). It is created readable only by
  you, and `main.py doctor` warns if you point it inside a git repository.
- **Your keys stay in `.env`**, which git ignores. The repo only has `.env.example`, with names and no values.
- **Only your number gets answers** (`WHATSAPP_ALLOWED_NUMBERS`), and every webhook call must carry Meta's signature.
- **Voice notes and videos are transcribed on your machine.** What you send is processed by the model provider
  you choose (Anthropic, Azure, or another), under that provider's terms.
- Logs never contain message text; phone numbers are shortened to their last 4 digits.

## How it works

```
WhatsApp ──> Meta ──> tunnel (ngrok) ──> main.py whatsapp ──> agent ──> model
                                             │                  │
                              voice note ──> whisper.cpp       skills/ + your private data folder
```

The agent keeps, per person: a profile, learned style preferences, voice samples, the last 30 messages and the
posts they published. The model works through tools (load a skill, create a file, remember a preference, check
what it can do...). Tools that would act on your accounts never run directly: they send you a **confirmation
card** with the exact content and two buttons, **Confirm** and **Cancel**. Asking for a change replaces the card;
cancelling asks what to change. Everything confirmed is written to an audit log. Facts and numbers must come from
you, your files or your connected accounts, never from the model. Design: [`docs/specs/agent-platform.md`](docs/specs/agent-platform.md)
and [`docs/adr/`](docs/adr/).

## Install

Needs Python 3.10+, ffmpeg and whisper.cpp. Python packages: the Anthropic SDK, `fpdf2` and `python-docx` (to
create files), `pypdf` and `defusedxml` (to read them). On macOS:

```sh
brew install ffmpeg whisper.cpp
git clone <this repo> && cd <repo>
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
scripts/download-whisper-model.sh          # ~570 MB, multilingual, saved in your data folder
cp .env.example .env                       # then fill it in (see below)
.venv/bin/python main.py doctor            # says what is missing
.venv/bin/python main.py chat              # try it in the terminal first
```

On Linux, install `ffmpeg` and build whisper.cpp (`whisper-cli`) from source, then follow the same steps.

### Model

Set `ANTHROPIC_API_KEY` (default provider, model `claude-opus-5-5`), or:

| Provider | Settings |
|---|---|
| Anthropic API | `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, optional `LLM_MODEL` |
| Azure (Anthropic-compatible) | `LLM_PROVIDER=azure`, `AZURE_CLAUDE_ENDPOINT`, `AZURE_CLAUDE_API_KEY`, `AZURE_CLAUDE_DEPLOYMENT` |
| OpenAI-compatible | `LLM_PROVIDER=openai`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `LLM_MODEL` |
| Your Claude plan, through Claude Code | `LLM_PROVIDER=claude-code`, optional `LLM_MODEL` (`sonnet`, `opus`) |

**Using your Claude plan.** With `claude-code`, Alter runs your own installed and signed-in
[Claude Code](https://claude.com/claude-code) (`claude -p`) with no built-in tools and no settings, so usage
counts against your Claude plan instead of an API key. Alter never reads, stores or asks for your Claude login:
sign in once with `claude` yourself, and `main.py doctor` checks it. Anthropic's terms describe plan usage for
ordinary, individual use of Claude Code; check them and use this only for yourself. Each reply takes a few
seconds longer than with the API.

**When the limit is reached.** Set `LLM_FALLBACK_PROVIDER` (for example `anthropic` with an API key) and Alter
answers with it while your plan is at its limit. Without one, Alter replies with the provider's own limit message.

### WhatsApp

1. Create an app at [developers.facebook.com](https://developers.facebook.com) and add the **WhatsApp** product.
   Meta gives you a test number. In *API Setup*, add your own phone as a recipient.
2. Copy into `.env`: the access token (`WHATSAPP_TOKEN`; use a permanent System User token for daily use),
   the *Phone number ID* (`WHATSAPP_PHONE_NUMBER_ID`) and the *App secret* from *App settings > Basic*
   (`WHATSAPP_APP_SECRET`). Invent a `WHATSAPP_VERIFY_TOKEN`. Put your number in `WHATSAPP_ALLOWED_NUMBERS`:
   anyone else is ignored.
3. Expose port 8080 with a fixed public URL. With [ngrok](https://ngrok.com) (free static domain):
   `brew install ngrok/ngrok/ngrok`, `ngrok config add-authtoken <token>`, and set `NGROK_DOMAIN`.
4. Run it: `.venv/bin/python main.py whatsapp` and, in another terminal, `ngrok http --url=$NGROK_DOMAIN 8080`.
5. In Meta, *WhatsApp > Configuration > Webhook*: callback URL `https://<your-domain>/webhook`, the same verify
   token, and subscribe to the **messages** field.
6. Message your bot from WhatsApp. The first message starts a short setup.

You always write first, so replies stay inside WhatsApp's 24-hour customer service window and need no paid
templates. Meta's pricing for replies can change: check
[Meta's pricing page](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing).

### Keep it running (macOS)

```sh
scripts/install-macos.sh               # bot + tunnel as LaunchAgents, restarted if they crash
sudo pmset -c sleep 0 disksleep 0      # don't sleep while plugged in
scripts/install-macos.sh --uninstall
```

Logs go to `~/Library/Logs/Alter/`. With the lid closed, a Mac only stays awake with an external
display connected.

### Slack (optional)

`pip install slack-bolt`, set `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`, and run `.venv/bin/python main.py slack`.
The Slack app needs Socket Mode, the `message.im` and `app_mention` events, the `chat:write`, `im:write` and
`files:write` scopes, and the Messages tab enabled. In Slack it reads text only, but it can send files.

## Files

| You send | What happens |
|---|---|
| Photo, screenshot, sticker | The model sees the image (large screenshots are scaled down first) |
| PDF | Sent to Claude as is, with its images and tables; other models get its text |
| Word, Excel, PowerPoint, txt, csv, json, srt... | Text is extracted locally |
| Voice note, video | Transcribed locally with whisper.cpp |

Files are kept in `<data folder>/users/<id>/files/`; the last three messages with images or PDFs go to the model as they
are, older ones as their extracted text. Limit: 25 MB per file. The assistant creates `.pdf`, `.docx`, `.md`,
`.txt` and `.csv` files in `<data folder>/users/<id>/outbox/` and sends them as WhatsApp documents. The PDF uses Arial on
macOS or DejaVu on Linux (`fonts-dejavu`); emojis are not drawn in PDFs.

## Using it

Talk to it normally: "voice note about what I shipped today, make it a LinkedIn post", "shorter", "the
second one but less formal", "I posted the first one on X". Commands: `help`, `preferences`,
`reset preferences`, `profile`, `skills` (also in Spanish: `ayuda`, `preferencias`...).

## Skills

`skills/` holds the bundled skills. Add your own anywhere with `EXTRA_SKILLS_DIRS`: each skill is a folder
with a `SKILL.md` (frontmatter `name` and `description`, then the instructions) and optional data files
(JSON, Markdown, text) that are sent along with it. Scripts in a skill folder are never run. Skills that need research data (`ig-viral`, `yt-viral`) are not
bundled: the assistant does not browse.

| Folder | Skills | Source |
|---|---|---|
| `skills/x` | `x-post` | this project |
| `skills/linkedin` | `li-post`, `li-human`, `li-carousel`, `li-repurpose`, `li-comment`, `li-reply`, `li-plan`, `li-dm`, `li-profile`, `li-audit`, `li-inbox` | [linkedin-agent-skill](https://github.com/Jakeschincariol/linkedin-agent-skill), MIT |
| `skills/instagram` | `ig-caption`, `ig-human`, `ig-carousel`, `ig-reel`, `ig-story`, `ig-repurpose`, `ig-comment`, `ig-reply`, `ig-plan`, `ig-dm`, `ig-profile`, `ig-audit` | [instagram-agent-skill](https://github.com/Jakeschincariol/instagram-agent-skill), MIT |
| `skills/youtube` | `yt-script`, `yt-package`, `yt-seo`, `yt-shorts`, `yt-chapters`, `yt-edit`, `yt-retention`, `yt-comment`, `yt-plan`, `yt-audit` | [youtube-agent-skill](https://github.com/Jakeschincariol/youtube-agent-skill), MIT |

Drafts are cleaned with `li-human`'s `humanize.py` (em dashes, invisible characters, stock AI words). Turn it
off with `HUMANIZE=false`.

## Development

```sh
.venv/bin/python -m unittest discover -s tests -t .
```

Tests use a fake model and a fake WhatsApp API, so they need no keys or network.

## License

MIT. Bundled skills keep their own licenses (see each folder's `LICENSE`).
