# Architecture decisions (ADR)

One file per decision that is hard to undo: `NNNN-short-title.md`.

```markdown
# NNNN. Title

Date: YYYY-MM-DD · Status: accepted | superseded by NNNN

## Context
The problem and its constraints.

## Decision
What was chosen.

## Consequences
What we gain, what we lose and what to watch.
```

The reasons come from whoever decides; they are never made up afterwards.

| ADR | Decision |
|---|---|
| [0001](0001-native-tool-use.md) | Actions are native tool calls; Claude Code requests them through structured output |
| [0002](0002-always-confirm-with-preview.md) | Every action on the user's behalf needs confirmation; public ones come with a preview image |
| [0003](0003-web-on-fastapi-with-magic-links.md) | One FastAPI server for the webhook and the web, reached through a magic link sent on WhatsApp |
| [0004](0004-sqlite-and-os-keychain.md) | SQLite for agent state, the OS keychain for connection secrets |
| [0005](0005-facts-come-from-data.md) | Facts and numbers come from data; reach estimates are computed from the user's own history |
| [0006](0006-hybrid-access-api-first.md) | Official APIs for acting; the user's browser session only for read-only gaps, opt-in per network |
| [0007](0007-one-voice-per-user.md) | One voice profile per user, adapted to each channel |
