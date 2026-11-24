# 0006. Hybrid access to social networks: official APIs first, the user's browser session for read-only gaps

Date: 2026-11-24 · Status: accepted

## Context
The goal is an assistant that can do on a social network what the user can do there. Official APIs are stable and
allowed, but expose less than the apps do (for example, fewer analytics for personal LinkedIn profiles, and X
features that depend on the API access level).

The alternative is to drive a real browser with the user's own signed-in session. That reaches everything the
user sees, but most networks' terms forbid automated access to their websites, some actively restrict accounts
that do it, it breaks when layouts change or a CAPTCHA appears, and an agent holding a full session can do
anything the user can, which raises the stakes of prompt injection.

## Decision
- **Acting** (publishing, replying, commenting, messaging) uses **official APIs only**.
- **Reading** uses the API when it provides the data. Where it does not, the user can enable a **browser session
  connection** for that network: **read-only**, **opt-in per network**, with a clear warning about the network's
  terms and the risk to the account, which the user must accept on the web.
- Alter **never stores passwords**. For a session connection it opens a browser and the user signs in by hand
  (including two-factor). Alter keeps that session in its own browser profile in the private data folder and
  only visits the domains of the networks the user enabled.
- **Google Workspace, Granola and other productivity tools always use their APIs.**
- **Alter states its real capabilities.** Every connection declares what it can read and do (API or session, and
  which metrics); Alter answers from that list and says plainly when it cannot do something instead of agreeing
  to everything. The web shows the same list per connection.

The user chose the hybrid option: the widest reach without putting their accounts at risk for publishing.

## Consequences
- Publishing never depends on browser automation, so it cannot get an account restricted for that reason.
- Session reading adds Playwright/Chromium as an optional dependency, needs upkeep when sites change, and carries
  a terms-of-service risk the user explicitly accepts per network.
- In session mode, page content is untrusted data like any other, and the session is never used for actions.
- Capabilities live in code next to each connector, so the assistant's answers about what it can do stay true.
