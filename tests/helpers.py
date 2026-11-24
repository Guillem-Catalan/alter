import tempfile
from pathlib import Path

from agent.brain import Agent
from agent.config import ROOT
from agent.skills import SkillLibrary, load_humanizer


class FakeLLM:
    """Returns scripted replies in order and records what it was sent."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls: list[tuple[list[str], list[dict]]] = []

    def complete(self, system_blocks, messages):
        self.calls.append((list(system_blocks), [dict(m) for m in messages]))
        return self.replies.pop(0) if self.replies else "ok"


def make_agent(*replies: str, humanize: bool = False, language: str = "en"):
    tmp = tempfile.TemporaryDirectory()
    library = SkillLibrary([ROOT / "skills"])
    llm = FakeLLM(*replies)
    agent = Agent(llm, library, Path(tmp.name), "TestBot", language,
                  load_humanizer(library) if humanize else None)
    return agent, llm, tmp
