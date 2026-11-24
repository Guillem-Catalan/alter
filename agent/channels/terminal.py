"""Chat with the agent from the terminal: the quickest way to try it without any chat app.

Attach files with `-f` (one-shot) or by starting a line with `/file <path>` (interactive).
"""
import shlex
from pathlib import Path

from ..brain import Agent, Reply
from ..files import UnsupportedFile, ingest
from ..store import UserStore

UID = "terminal"


def _show(reply: Reply) -> None:
    print(reply.text)
    for path in reply.files:
        print(f"[file] {path}")


def _ingest(agent: Agent, transcriber, ffmpeg: str, paths: list[Path]):
    user_dir = UserStore(agent.data_dir, UID).dir
    return [ingest(p.read_bytes(), "", p.name, user_dir, transcriber, ffmpeg) for p in paths]


def run(agent: Agent, transcriber, ffmpeg: str, message: str | None = None, files: list[Path] | None = None) -> None:
    if message is not None or files:
        _show(agent.handle(UID, message or "", "terminal", _ingest(agent, transcriber, ffmpeg, files or [])))
        return
    print("Type a message (Ctrl+D to quit). Attach a file with: /file <path> [message]")
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        attachments = []
        if line.startswith("/file "):
            parts = shlex.split(line[len("/file "):])
            if not parts:
                continue
            try:
                attachments = _ingest(agent, transcriber, ffmpeg, [Path(parts[0]).expanduser()])
            except (OSError, UnsupportedFile) as e:
                print(f"Could not attach it: {e}")
                continue
            line = " ".join(parts[1:])
        if line.strip() or attachments:
            _show(agent.handle(UID, line, "terminal", attachments))
            print()
