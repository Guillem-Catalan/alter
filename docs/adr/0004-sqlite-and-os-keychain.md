# 0004. SQLite for agent state, the OS keychain for connection secrets

Date: 2026-11-24 · Status: accepted

## Context
The agent needs durable state that JSON files handle poorly: pending actions waiting for confirmation, scheduled
jobs, the audit log, connected accounts and web sessions. It also needs to keep OAuth tokens and API keys for
the user's accounts. All of it lives on the user's own computer, in the private data folder.

## Decision
- **SQLite** (standard library) in the private data folder for pending actions, scheduled jobs, the audit log,
  connections (metadata only) and web sessions. Schema changes go through numbered migrations.
- **Secrets** (OAuth tokens, refresh tokens, per-connection API keys) go to the **operating system keychain**
  through the `keyring` package: macOS Keychain, Secret Service on Linux, Credential Manager on Windows. Never in
  SQLite, JSON or logs.
- Where no keychain is available (a headless Linux server), Alter stops and explains how to install one rather
  than falling back to plain files.
- Profiles, preferences, history and voice samples stay as they are for now; they can move to SQLite later.
- Disconnecting an account deletes its secrets and the data Alter stored from it.

## Consequences
- Copying the data folder does not copy access to the user's accounts.
- One more dependency (`keyring`); headless Linux needs a keyring backend.
- SQLite is single-file and needs no server, which keeps installation simple.
