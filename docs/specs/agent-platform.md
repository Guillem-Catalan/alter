# Spec: the agent platform (actions, confirmations, connections, web, schedules)

Date: 2026-11-24 · Status: build order step 1 (tools, agent loop, cards, SQLite, keychain, connector interface) built; the rest pending · Decisions: ADR 0001-0006

## Goal
Turn Alter from a writing assistant into a personal social media agent you talk to on WhatsApp: it reads from
your tools (meetings, email, calendar, documents), writes in your voice, acts on your social accounts always after
you confirm, and reports how your posts perform with real data, never invented. It stays self-hosted and open source: each person runs it on their own computer with their own keys.

## Scope
1. **Tool registry and agent loop** (ADR 0001): every capability is a tool; provider adapters for Anthropic,
   Azure, OpenAI-compatible and Claude Code (MCP bridge).
2. **Pending actions and confirmation cards** (ADR 0002), with preview images for public actions.
3. **Connector interface**: a common shape for every integration, with read and act tools.
4. **State and secrets** (ADR 0004): SQLite in the data folder, secrets in the OS keychain, audit log.
5. **Web app** (ADR 0003): setup and connections, behind the same public URL as the webhook, magic-link login.
6. **Scheduler**: scheduled actions, and post reports **only when the user asks** ("tell me every 2 hours how
   the post is doing, who comments").
7. **Performance and reach** (ADR 0005): metrics and comments read from the APIs and rendered by code; reach
   estimates computed from the user's own history.
8. First connectors, in this order: LinkedIn and X (act), Granola and Google Workspace (read), then Instagram
   and YouTube.

## Out of scope (for now)
- A hosted multi-user service. Alter runs on each user's computer.
- Acting through browser automation. Publishing, replying and messaging use official APIs only (ADR 0006).
- Acting without confirmation, under any setting.
- Group chats.
- Unrequested notifications: Alter never messages first unless the user asked for that report or reminder.

## Design

### Components

```
WhatsApp ─┐                         ┌─ tools: core (skills, files, memory)
Web ──────┼─> FastAPI app ─> Agent ─┼─ tools: connectors (read / act)
Scheduler ┘        │          │     └─ pending actions ─> confirmation card ─> executor ─> audit log
                   │          └─ model adapters: anthropic · azure · openai · claude-code (MCP)
                   └─ SQLite (data folder) + OS keychain (secrets)
```

### Tools
```python
@dataclass
class Tool:
    name: str             # "linkedin_publish_post"
    description: str      # what it does, for the model
    input_schema: dict    # JSON schema, strict
    kind: Literal["read", "act"]
    run: Callable[[Context, dict], ToolResult]          # read tools run directly
    preview: Callable[[Context, dict], Preview] | None  # act tools: card text and optional image
```
- Core tools replace today's tags: `load_skill`, `create_file`, `remember_preference`, `log_published`,
  `save_voice_sample`, `set_profile`, `send_web_link`.
- The agent loop sends the conversation and the tools, runs read tools and feeds results back, and stops when the
  model answers. A cap on tool calls per turn prevents loops.
- Content returned by read tools that comes from other people (emails, comments, DMs, transcripts) is fenced
  with a random per-call boundary (any boundary-like text inside is neutralised), and the system prompt says it
  is never instructions.
- Once a turn has read untrusted content, tools that change the user's memory (profile, preferences, voice
  samples, published log) are refused for the rest of that turn; the model must ask the user.

### Pending actions and confirmation
- Calling an act tool validates the input, stores a pending action `{id, user, tool, input, preview, status,
  expires_at}` and returns `{"status": "awaiting_confirmation", "id": ...}` to the model.
- The channel sends the card: summary (network, account, when), the exact content, attachments, the preview
  image, and two reply buttons, **Confirm · Cancel** (WhatsApp interactive message). Typing "yes" or "sí"
  confirms the latest card only if it is the only one pending.
- Confirm runs the action and writes the audit log; the result (link to the published post, or the error) is sent
  back.
- **Changes replace the card.** If the user asks for a change while a card is pending ("no, change the first
  line"), Alter withdraws that card (status `superseded`, its buttons stop working) and sends a new one with the
  change. There is never more than one live card for the same action.
- **Cancel asks why.** Cancel closes the card and Alter asks what should change; if the user says nothing needs
  to change, the conversation moves on.
- Cards expire after 24 h.
- Confirmations only come from the user's own messages on WhatsApp or the web, never from tool output.

### Previews
- One HTML template per surface (LinkedIn post, X post and thread, Instagram feed post and carousel, YouTube
  title and thumbnail), filled with the user's name, handle, avatar, text and media, mimicking each app's
  current layout and "see more" truncation.
- Rendered to PNG locally with Playwright (Chromium), optional. Without it the card is text only.
- Templates use no third-party logos or trademarks beyond what is needed to recognise the network.

### Connectors
```python
class Connector:
    id: str                     # "linkedin"
    name: str
    auth: Literal["oauth2", "api_key", "local", "browser_session"]
    scopes: list[str]
    capabilities: list[Capability]  # what it can read and do, and through which access (api / session)
    def tools(self) -> list[Tool]: ...
    def connect_url(self, session) -> str: ...      # OAuth: authorization URL with PKCE and state
    def complete(self, request) -> Connection: ...  # OAuth callback
    def disconnect(self, connection) -> None: ...   # revoke if possible, delete secrets and stored data
```
- Each connector lives in `agent/connectors/<id>.py` and registers its tools; a connector's tools are only
  offered to the model while that account is connected.
- Each user registers their own developer app on each platform (the web shows the exact steps and redirect URL).
- **Browser session connections** (ADR 0006): read-only, opt-in per network after accepting a warning about the
  network's terms. The user signs in by hand in a browser Alter opens; Alter keeps the session in its own browser
  profile in the data folder, never the password, and only visits that network's domains. Never used for actions.

### Capabilities
- Each connector declares its capabilities: what it can read (which metrics, comments, DMs) and do, and whether
  through the API or a session. A `capabilities` tool gives the model this list.
- The system prompt requires Alter to check capabilities before promising anything and to say plainly what it
  cannot do with the current connections ("with your personal LinkedIn profile I can see impressions and
  reactions, not who viewed the post").
- The web's Connections page shows the same list.

### State
SQLite tables: `pending_actions`, `audit_log`, `jobs`, `post_metrics`, `connections` (no secrets), `web_sessions`,
`magic_links`, `schema_version`. Secrets in the keychain under the service name `alter`, keyed by connection id.

### Web
Pages: sign-in landing (only reachable through a magic link), Overview (status of model, WhatsApp, tunnel,
connections, next scheduled jobs), Model (provider and key or subscription check), Connections (connect,
disconnect, scopes), Activity (audit log and pending cards, confirm or cancel from the web), Style (learned
preferences and voice samples, editable), Settings (language, sessions, sign out everywhere).

### Scheduler
A background task in the same process checks `jobs` every minute and runs due jobs: confirmed scheduled actions,
and **reports the user asked for**. A report has a target (a post, an account), a frequency, what to include
(metrics, new comments and who wrote them) and an end ("until Friday", "for 24 h", or until the user says stop).
Alter never creates a report or reminder on its own; it may offer one in a reply, and creates it only if the user
says yes. Every active report is listed on the web and can be stopped from WhatsApp ("stop the reports"). Messages that start a
conversation outside WhatsApp's 24-hour window need an approved template: the first version only notifies inside
the window and shows the rest in the web's Activity page; templates are a later decision.

### Performance, comments and reach (ADR 0005)
- Read tools per connector: `<network>_post_metrics`, `<network>_post_comments`, `<network>_recent_posts`.
  Each result carries its source and timestamp.
- A report message is built by code: figures, deltas since the last report, and new comments with author and text
  (comment text is untrusted data). The model may add a short comment below, without numbers of its own.
- Reach estimate (`estimate_reach`): code takes the user's last posts of the same network and format with metrics,
  and returns a range (25th-75th percentile of impressions, adjusted for posting hour and day when there is
  enough data) plus the sample size. Fewer than 10 comparable posts: no estimate, and Alter says why.
- Metrics history is stored in SQLite (`post_metrics` table) so estimates improve over time and reports show
  deltas. Availability per platform and account type is listed on the Connections page.
- The system prompt forbids stating facts without a source and requires saying "I don't know" or asking.

### Model providers
`claude-code` runs the user's installed, unmodified Claude Code with `-p`, no built-in tools, no user settings,
and Alter's MCP server; Alter never reads or stores Claude credentials. When it reports a usage limit, Alter uses
`LLM_FALLBACK_PROVIDER` if set, or relays Claude's own limit message.

## Risks
| Risk | Mitigation |
|---|---|
| Prompt injection from emails, comments or meeting notes | Untrusted-data wrapping, act tools only create cards, confirmations only from the user |
| A public web on a personal computer | Magic links, CSRF, rate limits, secure cookies, no secrets displayed, allow-listed numbers only |
| Leaked tokens | OS keychain, never logged; disconnect revokes and deletes |
| Platform API changes, quotas and app review (Instagram, YouTube) | Connectors isolated behind one interface; docs list each platform's limits |
| Claude Code provider rules | Documented as personal use; Alter never handles Claude credentials |
| Preview mismatch with the real app | Labelled as a preview; templates versioned and tested with screenshots |
| The model states a number or fact that is not real | Figures rendered by code from API data; estimates computed by code; prompt requires sources |
| Platform APIs expose few metrics (personal LinkedIn, X tiers) | Optional read-only session connection; Alter states what each connection can read; no estimate without data |
| Session connections break the network's terms or get blocked | Read-only, opt-in per network with an explicit warning, never used to act |

## How it will be verified
- Unit tests per tool, connector (against recorded fake APIs) and provider adapter (fake servers, as today).
- A test proving that no act tool can execute without a confirmation coming from the user, including when the
  model is told to confirm by text inside a tool result.
- Web: FastAPI test client for magic links (single use, expiry, rate limits), sessions, CSRF and OAuth state.
- Previews: screenshot tests of each template on desktop and mobile widths.
- Reports and estimates: tests that every figure in a report equals the API data it came from, that estimates
  match a known sample, and that below 10 posts no estimate is given.
- Scheduler: a test that no report or reminder exists unless a user message created it.
- Capabilities: tests that act tools never use a session connection, and that the model is given only the
  capabilities of connected accounts.
- End to end on a real phone: a voice note becomes a LinkedIn post card with a preview; confirming publishes it;
  the audit log shows it.

## Build order
1. Tool registry, agent loop and provider adapters (replacing tags), SQLite, pending actions and cards. Base for
   everything else, built first in a single session.
2. In parallel afterwards: Claude Code provider and MCP bridge · web app and magic links · LinkedIn · X ·
   Granola · Google Workspace · previews · installer.
3. Reports on request and reach estimates (need connectors with metrics). Instagram and YouTube connectors.
