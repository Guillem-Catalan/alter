"""Skills: folders with a SKILL.md (Claude Code / Agent Skills format) that the agent loads on demand.

The model only sees each skill's name and description. When it needs one it asks for it with
`[SKILL: name]`, and the full SKILL.md plus its data files (JSON, Markdown, text) are added to
its instructions. Scripts are never run: the model applies their method by hand.
"""
import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path

DATA_SUFFIXES = (".json", ".md", ".txt", ".csv")
MAX_DATA_FILE_BYTES = 40_000


@dataclass
class Skill:
    name: str
    description: str
    path: Path

    def body(self) -> str:
        text = (self.path / "SKILL.md").read_text()
        parts = [_strip_frontmatter(text).strip()]
        for f in sorted(self.path.iterdir()):
            if f.name == "SKILL.md" or f.suffix not in DATA_SUFFIXES or not f.is_file():
                continue
            if f.stat().st_size > MAX_DATA_FILE_BYTES:
                continue
            parts.append(f'<file name="{f.name}">\n{f.read_text().strip()}\n</file>')
        return "\n\n".join(parts)


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def parse_frontmatter(text: str) -> dict[str, str]:
    """Read top-level `key: value` pairs, including folded (`>`, `>-`) and literal (`|`) blocks."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    lines = text[3:end].strip("\n").splitlines()
    result: dict[str, str] = {}
    i = 0
    while i < len(lines):
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", lines[i])
        i += 1
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value in (">", ">-", "|", "|-", ""):
            block = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            joiner = "\n" if value.startswith("|") else " "
            value = joiner.join(part for part in block if part)
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value
    return result


class SkillLibrary:
    def __init__(self, dirs: list[Path]):
        self.skills: dict[str, Skill] = {}
        for base in dirs:
            if not base.is_dir():
                continue
            for skill_md in sorted(base.rglob("SKILL.md")):
                meta = parse_frontmatter(skill_md.read_text())
                name = meta.get("name") or skill_md.parent.name
                if name not in self.skills:
                    self.skills[name] = Skill(name, meta.get("description", ""), skill_md.parent)

    def __contains__(self, name: str) -> bool:
        return name in self.skills

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def index(self) -> str:
        return "\n".join(f"- {s.name}: {s.description}" for s in self.skills.values())


def load_humanizer(library: SkillLibrary):
    """Return a `text -> text` cleaner from the li-human skill's humanize.py, or None if it is missing.

    It strips invisible characters, swaps em dashes and curly quotes, and replaces stock AI words.
    """
    skill = library.get("li-human")
    if not skill or not (skill.path / "humanize.py").exists():
        return None
    spec = importlib.util.spec_from_file_location("skill_humanize", skill.path / "humanize.py")
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    lexicon = module.load_lexicon()

    def clean(text: str) -> str:
        cleaned, _report = module.humanize(text, lexicon)
        return cleaned.rstrip("\n")

    return clean
