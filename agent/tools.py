"""Tools: every capability the model can use, defined once and offered to every provider (ADR 0001).

Kinds:
- internal: changes only Alter's own state (memory, files for the user). Runs right away.
- read: fetches data from outside (a meeting, an inbox, post metrics). Runs right away.
- act: does something visible to others or changes an external account. Never runs when the model
  calls it: it becomes a pending action that the user must confirm (ADR 0002).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")  # accepted by both Anthropic and OpenAI


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    untrusted: bool = False  # text written by other people (emails, comments): data, never instructions
    source: str = ""         # where untrusted content came from, shown to the model


@dataclass
class Card:
    """What the user sees before confirming an action."""
    summary: str              # one or two lines: what, where, when
    content: str = ""         # the exact text that will be published or sent
    image: str = ""           # optional preview image path


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    kind: Literal["internal", "read", "act"]
    run: Callable[[Any, dict], ToolResult | str]
    card: Callable[[Any, dict], Card] | None = None
    capability: str = ""      # short line for the capabilities list ("Publish a post on LinkedIn")

    def __post_init__(self):
        if not NAME_RE.match(self.name):
            raise ValueError(f"invalid tool name {self.name!r}")
        if self.kind == "act" and self.card is None:
            raise ValueError(f"act tool {self.name} needs a card")

    def schema_for_model(self) -> dict:
        """The schema the model sees; act tools get an optional `replaces` list of card ids."""
        schema = dict(self.input_schema)
        if self.kind == "act":
            props = dict(schema.get("properties", {}))
            props["replaces"] = {
                "type": "array", "items": {"type": "string"},
                "description": "Ids of pending cards this one replaces, when the user asked for a change.",
            }
            schema["properties"] = props
        return schema


def obj(properties: dict, required: list[str] | None = None) -> dict:
    """Shorthand for a strict object schema."""
    return {"type": "object", "properties": properties, "required": required or list(properties),
            "additionalProperties": False}


TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def validate(schema: dict, value: Any, path: str = "input") -> list[str]:
    """Check a value against the JSON schema subset our tools use. Returns a list of problems."""
    problems: list[str] = []
    expected = schema.get("type")
    if expected:
        py = TYPES[expected]
        if isinstance(value, bool) and expected in ("integer", "number"):
            return [f"{path} must be a {expected}"]
        if not isinstance(value, py):
            return [f"{path} must be a {expected}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path} must be one of {schema['enum']}")
    if isinstance(value, str):
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            problems.append(f"{path} is longer than {schema['maxLength']} characters")
        if "minLength" in schema and len(value) < schema["minLength"]:
            problems.append(f"{path} is shorter than {schema['minLength']} characters")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{path}.{key} is required")
        for key, item in value.items():
            if key in props:
                problems += validate(props[key], item, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                problems.append(f"{path}.{key} is not allowed")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            problems += validate(schema["items"], item, f"{path}[{i}]")
    return problems


@dataclass
class Registry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def add(self, *tools: Tool) -> None:
        for tool in tools:
            if tool.name in self.tools:
                raise ValueError(f"duplicate tool {tool.name}")
            self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def all(self) -> list[Tool]:
        return list(self.tools.values())
