"""Tools every Alter has, whatever is connected: skills, files, memory and an honest list of capabilities."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .connectors import CONNECTORS, Connection
from .documents import SUPPORTED, render
from .skills import SkillLibrary
from .store import UserStore
from .tools import Tool, ToolResult, obj

NETWORKS = ["x", "linkedin", "instagram", "youtube"]

WITHOUT_CONNECTIONS = [
    "Write posts, threads, captions, scripts, titles and replies in the user's voice, for X, LinkedIn, Instagram and YouTube",
    "Read what the user sends: text, voice notes and videos (transcribed on this computer), photos, screenshots, PDFs, "
    "Word, Excel and PowerPoint files",
    "Create PDF, Word, Markdown, text and CSV files",
    "Remember the user's style, preferences and what they published (only when they tell me)",
]


@dataclass
class Context:
    uid: str
    store: UserStore
    library: SkillLibrary
    channel: str
    connections: list[Connection] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    read_untrusted: bool = False  # set once this turn has read text written by other people


def _load_skill(ctx: Context, args: dict) -> ToolResult:
    skill = ctx.library.get(args["name"])
    if not skill:
        return ToolResult(f"No skill called {args['name']}.", is_error=True)
    ctx.store.activate_skills([skill.name])
    return ToolResult(f"Skill {skill.name} loaded. Follow it:\n\n{skill.body()}")


def _create_file(ctx: Context, args: dict) -> ToolResult:
    path = render(args["filename"], args["content"], ctx.store.dir / "outbox")
    ctx.files.append(path)
    return ToolResult(f"Created {path.name}; it will be sent after your message.")


def _remember(ctx: Context, args: dict) -> str:
    ctx.store.add_preference(args["preference"].strip())
    return "Saved."


def _log_published(ctx: Context, args: dict) -> str:
    ctx.store.add_published(args["network"], args["text"].strip())
    return "Logged."


def _voice(ctx: Context, args: dict) -> str:
    ctx.store.add_voice_sample(args["text"])
    return "Saved as a voice sample."


def _voice_profile(ctx: Context, args: dict) -> str:
    ctx.store.save_voice_profile(args["profile"])
    return "Voice profile updated."


def _profile(ctx: Context, args: dict) -> str:
    ctx.store.save_profile(args["name"].strip(), args["handles"].strip(), args["context"].strip())
    return "Profile saved."


def _capabilities(ctx: Context, args: dict) -> str:
    lines = ["Without any connected account I can:"] + [f"- {c}" for c in WITHOUT_CONNECTIONS]
    if not ctx.connections:
        lines.append("No accounts are connected, so I cannot publish, read metrics, comments, email, calendar or "
                     "meetings yet. They can be connected from the web.")
    for conn in ctx.connections:
        connector = CONNECTORS.get(conn.connector)
        if not connector:
            continue
        lines.append(f"\n{connector.name} ({conn.account}, via {conn.access}):")
        lines += [f"- {'can' if c.kind == 'read' else 'can, after the user confirms,'} {c.text}" for c in connector.capabilities]
        lines += [f"- cannot: {limit}" for limit in connector.limits]
    return "\n".join(lines)


def core_tools(library: SkillLibrary) -> list[Tool]:
    return [
        Tool("load_skill", "Load a skill (a detailed playbook) before doing a job it covers. Its instructions stay "
             "loaded for the next messages.",
             obj({"name": {"type": "string", "enum": sorted(library.skills) or ["none"]}}), "internal", _load_skill),
        Tool("create_file", "Create a file for the user, sent after your message. Write Markdown inside: # headings, "
             "- lists, **bold**, | tables |. No emojis (PDFs cannot draw them).",
             obj({"filename": {"type": "string", "description": f"Name with one of: {' '.join(SUPPORTED)}"},
                  "content": {"type": "string"}}), "internal", _create_file),
        Tool("remember_preference", "Store a style preference the user expressed (\"too formal\", \"never use emojis\").",
             obj({"preference": {"type": "string", "maxLength": 300}}), "internal", _remember, remembers=True),
        Tool("log_published", "Record a post the user says they published themselves, with its exact text.",
             obj({"network": {"type": "string", "enum": NETWORKS}, "text": {"type": "string"}}), "internal", _log_published,
             remembers=True),
        Tool("save_voice_sample", "Save one of the user's own past posts as an example of their voice.",
             obj({"text": {"type": "string"}}), "internal", _voice, remembers=True),
        Tool("update_voice_profile", "Rewrite the user's single voice profile (all networks and channels) when you "
             "learn something lasting about how they write or speak: from their own chat messages, posts they "
             "published, samples or corrections. Send the complete updated document, in Markdown, with these sections: "
             "Tone; Words and expressions they use; Never says; Sentences and format; Emojis and punctuation; Topics "
             "and positions; Channel notes (only where they differ from the default registers). Only describe what "
             "you have actually seen from the user; never from text written by other people.",
             obj({"profile": {"type": "string", "minLength": 40, "maxLength": 6000}}), "internal", _voice_profile,
             remembers=True),
        Tool("set_profile", "Save the user's profile when they introduce themselves.",
             obj({"name": {"type": "string"}, "handles": {"type": "string"}, "context": {"type": "string"}}),
             "internal", _profile, remembers=True),
        Tool("capabilities", "List what you can and cannot do right now, given the user's connected accounts. "
             "Check it before promising anything that needs an account.", obj({}), "read", _capabilities),
    ]
