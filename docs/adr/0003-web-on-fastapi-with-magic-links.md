# 0003. One FastAPI server for the webhook and the web, reached through a magic link sent on WhatsApp

Date: 2026-11-24 · Status: accepted

## Context
Users need a page to set Alter up and connect accounts: the model provider, WhatsApp, social networks, Google
Workspace, Granola. The page must be reachable from any device, not only the computer Alter runs on.
Connecting accounts with OAuth also needs a public HTTPS callback URL.

Alter already exposes a WhatsApp webhook through a tunnel with a fixed public domain. The current server is
built on the standard library, which is fine for a signed webhook but not for a public web app with sessions
and forms.

## Decision
- A single **FastAPI** app (served by uvicorn) handles the WhatsApp webhook, the web pages and the OAuth
  callbacks, behind the same public URL. Pages are server-rendered HTML (Jinja2), with no JavaScript build step.
- **No passwords.** The user asks Alter for the link on WhatsApp and gets a single-use **magic link** that
  expires in 10 minutes. Only numbers in the allow-list can request one. Opening it creates a session cookie
  (`HttpOnly`, `Secure`, `SameSite=Lax`, 7 days, revocable from WhatsApp).
- Every form has CSRF protection; magic-link and login endpoints are rate limited; secrets are never shown in
  full after they are saved.
- OAuth uses the authorization code flow with PKCE and a `state` bound to the session.

The user chose FastAPI so a public web is built on tested security pieces instead of hand-written ones.

## Consequences
- New dependencies: `fastapi`, `uvicorn`, `jinja2`, `python-multipart`, `itsdangerous`.
- The current standard-library webhook server is replaced; its tests move to FastAPI's test client.
- The web is only as available as the computer running Alter and its tunnel.
- Anyone who gets a valid magic link within 10 minutes gets in: the link travels only over WhatsApp to the
  owner's number, and can be used once.
