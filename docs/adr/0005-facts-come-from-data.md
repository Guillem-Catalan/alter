# 0005. Facts and numbers come from data, never from the model

Date: 2026-11-24 · Status: accepted

## Context
Alter reports how posts perform (impressions, reactions, comments, who commented) and estimates the reach of a
draft before it is published. Language models can produce plausible numbers and facts that are not true. For a
social media assistant that people use to decide what to publish, an invented metric or a made-up fact under the
user's name is worse than no answer.

## Decision
- **Numbers are rendered by code.** Metrics come from the platforms' APIs through read tools, and the message
  text with the figures is built by code from that data, with its source and time ("LinkedIn, 14:05"). The model
  may comment on them, but never writes or changes a figure.
- **Reach estimates are computed, not guessed.** An estimate is a range calculated by code from the user's own
  past posts on that network (same format, recent period), with the sample size shown. Below a minimum sample
  (10 comparable posts) Alter says it cannot estimate yet. The model can explain the drivers but not the number.
- **Every factual claim needs a source**: a tool result, a connected account, a file the user sent, or something
  the user said. The system prompt requires the model to say "I don't know" or ask when there is no source, and
  drafts never include invented metrics, clients, quotes or results.
- Facts taken from outside sources (emails, meeting notes, documents) are cited by name and date in the reply.

The user asked for an assistant that never invents anything and where everything is checked against real data.

## Consequences
- Reports and estimates are trustworthy by construction, not by prompt.
- Estimates need history: new accounts, or networks whose API does not expose the needed metrics, get no
  estimate.
- Metric availability differs by platform and account type (personal vs business); the web shows what each
  connection can and cannot read.
- Report and estimate formats are code, so they change through pull requests, not prompt edits.
