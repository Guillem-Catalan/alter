# 0007. One voice per user, adapted to each channel

Date: 2026-11-24 · Status: accepted

## Context
Skills for each network kept their own idea of the user's voice and asked for sample posts before writing.
People have one voice and change register depending on where they speak.

## Decision
- Each user has a single voice profile (`voice_profile.md` in their data folder), built from how they write to
  Alter in the chat, posts they published on any network, samples and corrections. Previous versions are kept.
- The model adapts the register per channel (X daily and short, LinkedIn crafted, Instagram visual, YouTube
  spoken, email formal, WhatsApp simple); channel notes in the profile override these defaults.
- Alter never holds back a draft to ask for samples. Code asks the model to write the profile after the user's
  third message and to refresh it every 15 messages; it can never be written after reading untrusted text.

The user asked for one voice per person, adapted to each social network and channel.

## Consequences
- Every skill and channel shares the same voice; skills asking for a voice.md use the profile instead.
- The profile is a model-written summary: it is shown and editable on the web (planned), and versions can be restored.
