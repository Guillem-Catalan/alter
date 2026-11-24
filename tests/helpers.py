import tempfile
from pathlib import Path

from agent.brain import Agent
from agent.config import ROOT
from agent.llm import Turn
from agent.skills import SkillLibrary, load_humanizer
from agent.tools import ToolCall


def call(tool: str, /, **args) -> Turn:
    """A model turn that calls one tool."""
    return Turn("", [ToolCall(f"call_{tool}_{len(args)}", tool, args)])


class FakeLLM:
    """Returns scripted turns in order (a str is a plain text answer) and records every request."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.calls: list[tuple[list[str], list[dict], list]] = []

    def respond(self, system_blocks, messages, tools):
        self.calls.append((list(system_blocks), [dict(m) for m in messages], list(tools)))
        turn = self.turns.pop(0) if self.turns else "ok"
        return Turn(turn) if isinstance(turn, str) else turn


def make_agent(*turns, humanize: bool = False, language: str = "en"):
    tmp = tempfile.TemporaryDirectory()
    library = SkillLibrary([ROOT / "skills"])
    llm = FakeLLM(*turns)
    agent = Agent(llm, library, Path(tmp.name), "TestBot", language, load_humanizer(library) if humanize else None)
    return agent, llm, tmp
