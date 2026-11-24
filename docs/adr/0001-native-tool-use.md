# 0001. Actions are native tool calls, bridged through MCP for the Claude Code provider

Date: 2026-11-24 · Status: accepted

## Context
Alter is moving from "ask the model for text" to an agent that reads from the user's tools (meetings, email,
calendar) and acts on their accounts (publish, reply, schedule). The model needs a way to request those
actions with exact, structured arguments.

Today the model asks for skills and files with text tags (`[SKILL: name]`, `[FILE: name] ... [/FILE]`). Tags work
with every provider, including Claude Code (`claude -p`) running on the user's subscription, but they get
fragile once actions carry many fields (network, account, text, media, time).

Alter supports several model providers: the Anthropic API, an Anthropic-compatible Azure endpoint, any
OpenAI-compatible API, and Claude Code on the user's own subscription.

## Decision
Every capability is a **tool** with a name, a description and a JSON schema, defined once in a tool registry.
Provider adapters expose the same registry:

- Anthropic and Azure: native `tool_use`.
- OpenAI-compatible: function calling.
- Claude Code: Alter starts a small local MCP server (stdio) with the same tools and passes it to `claude -p`
  with `--mcp-config` and `--strict-mcp-config`; Claude Code's built-in tools stay disabled (`--tools ""`).

The existing tags become tools (`load_skill`, `create_file`, `remember_preference`, `log_published`,
`save_voice_sample`, `set_profile`).

The user chose this option for reliability.

## Consequences
- Arguments are validated in code against the tool's schema before anything runs, the same way for every
  provider; invalid calls go back to the model as errors so it can retry.
- One tool definition serves every provider; adding a connector means adding tools, not prompt syntax.
- More code than tags: an adapter per provider and an MCP server for Claude Code (adds the `mcp` package).
- Tool results go back to the model in the same turn, so reading (a meeting, an inbox) works in one exchange.
- Tools that act outside Alter never execute from the model's call directly: see ADR 0002.
