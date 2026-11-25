# 0002. Every action on the user's behalf needs confirmation; public ones come with a preview image

Date: 2026-11-24 · Status: accepted

## Context
Alter will publish posts, reply to comments, send DMs and emails, and schedule work on the user's accounts.
An agent that acts without asking breaks trust with a single mistake, and agents that read untrusted text
(emails, comments, DMs) can be tricked by instructions hidden in it.

## Decision
- Tools are classified as **read** (no side effects outside Alter) or **act** (anything visible to others or
  that changes an external account).
- An act tool never executes when the model calls it. It creates a **pending action** with the exact payload
  and returns "waiting for the user's confirmation" to the model.
- Alter sends the user a **confirmation card**: what will happen, where, from which account, the exact text and
  media, and when. For anything public (posts, replies, comments) the card includes a **preview image** that
  mimics how it will look in that app, rendered locally from an HTML template.
- The action runs only after an explicit yes for that specific card (a WhatsApp reply button, or typing yes).
  Asking for a change withdraws the card and sends a new one; Cancel closes it and Alter asks what to change.
  Cards expire after 24 h.
- Scheduled actions are confirmed when they are scheduled, with the final content; a later change needs a new
  confirmation.
- Content read from email, comments, DMs, meetings or the web is data, never instructions. It is passed to the
  model inside a random, unguessable fence, it can never confirm an action, and while it is in the conversation the
  model cannot change the user's memory (until it leaves the history or the user starts a new chat).
- Every executed action is written to an audit log (what, where, when, confirmed by which message).

The user decided that confirmation is always required, with no option to turn it off, and asked for a preview
of how the result will look in each app.

## Consequences
- No action can happen by accident or through prompt injection; the worst case is an unwanted card.
- One more step for every action. Batches ("publish these three") get one card each, by design.
- Previews are an approximation: no platform offers an official preview API. Templates need upkeep when apps
  change their look.
- Rendering previews needs a headless browser (Playwright with Chromium, ~150 MB, optional): without it the card
  is text only.
