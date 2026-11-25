"""Per-user state on disk: profile, learned preferences, chat history, published posts and voice samples."""
import json
import re
import threading
from datetime import date, datetime
from pathlib import Path

from .config import private_dir

HISTORY_LIMIT = 30
PREFERENCES_LIMIT = 20
PUBLISHED_LOG_LIMIT = 50
ACTIVE_SKILLS_LIMIT = 3
VOICE_HISTORY_LIMIT = 10
VOICE_FIRST_AFTER = 3    # user messages before the first voice profile
VOICE_REFRESH_EVERY = 15  # user messages between refreshes

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def user_lock(uid: str) -> threading.Lock:
    """One lock per user so two messages from the same person are answered in order."""
    with _locks_guard:
        return _locks.setdefault(uid, threading.Lock())


class UserStore:
    def __init__(self, data_dir: Path, uid: str):
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", uid)
        if not safe.strip("._"):
            raise ValueError(f"invalid user id: {uid!r}")
        private_dir(data_dir)
        private_dir(data_dir / "users")
        self.dir = private_dir(data_dir / "users" / safe)
        (self.dir / "voice_pack").mkdir(exist_ok=True)

    def _load(self, name, default):
        path = self.dir / name
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return default

    def _save(self, name, data) -> None:
        path = self.dir / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(path)

    # profile
    def profile(self) -> dict:
        profile = self._load("profile.json", {"name": "", "handles": "", "context": "", "setup_done": False})
        # Profiles saved by the first, X-only version stored a single `x_handle`.
        profile.setdefault("handles", profile.get("x_handle", ""))
        return profile

    def save_profile(self, name: str, handles: str, context: str) -> None:
        self._save("profile.json", {"name": name, "handles": handles, "context": context, "setup_done": True})

    # preferences
    def preferences(self) -> list[str]:
        return self._load("preferences.json", [])

    def add_preference(self, text: str) -> None:
        prefs = self.preferences()
        if text not in prefs:
            prefs.append(text)
            self._save("preferences.json", prefs[-PREFERENCES_LIMIT:])

    def reset_preferences(self) -> None:
        self._save("preferences.json", [])

    # history
    def history(self) -> list[dict]:
        return self._load("history.json", [])

    def save_history(self, history: list[dict]) -> None:
        history = history[-HISTORY_LIMIT:]
        # The API needs the conversation to start with a user turn.
        while history and history[0].get("role") != "user":
            history = history[1:]
        self._save("history.json", history)

    def has_started(self) -> bool:
        return (self.dir / "history.json").exists()

    # published posts
    def published_today(self) -> list[dict]:
        data = self._load("published.json", {"date": "", "posts": []})
        return data.get("posts", []) if data.get("date") == str(date.today()) else []

    def recent_published(self, n: int = 5) -> list[dict]:
        return self._load("all_published.json", [])[-n:]

    def add_published(self, network: str, text: str) -> None:
        now = datetime.now()
        today = self._load("published.json", {"date": "", "posts": []})
        if today.get("date") != str(date.today()):
            today = {"date": str(date.today()), "posts": []}
        today["posts"].append({"network": network, "text": text, "time": now.strftime("%H:%M")})
        self._save("published.json", today)
        log = self._load("all_published.json", [])
        log.append({"network": network, "text": text, "date": str(date.today()), "time": now.strftime("%H:%M")})
        self._save("all_published.json", log[-PUBLISHED_LOG_LIMIT:])

    # voice samples
    def voice_pack(self) -> str:
        chunks = [
            f"### {f.stem}\n{f.read_text().strip()}"
            for f in sorted((self.dir / "voice_pack").glob("*"))
            if f.suffix in (".md", ".txt")
        ]
        return "\n\n".join(chunks)

    # message counter, to know when the voice profile is due
    def count_user_message(self) -> int:
        stats = self._load("stats.json", {"user_messages": 0, "voice_at": None})
        stats["user_messages"] += 1
        self._save("stats.json", stats)
        return stats["user_messages"]

    def voice_due(self) -> bool:
        stats = self._load("stats.json", {"user_messages": 0, "voice_at": None})
        if not self.voice_profile():
            return stats["user_messages"] >= VOICE_FIRST_AFTER
        return stats["voice_at"] is None or stats["user_messages"] - stats["voice_at"] >= VOICE_REFRESH_EVERY

    # the user's single voice profile, shared by every network and channel
    def voice_profile(self) -> str:
        path = self.dir / "voice_profile.md"
        return path.read_text() if path.exists() else ""

    def save_voice_profile(self, text: str) -> None:
        """Replace the profile, keeping the last versions so a bad update can be undone."""
        path = self.dir / "voice_profile.md"
        history = self.dir / "voice_profile_history"
        if path.exists():
            history.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            (history / f"{stamp}.md").write_text(path.read_text())
            for old in sorted(history.glob("*.md"))[:-VOICE_HISTORY_LIMIT]:
                old.unlink()
        path.write_text(text.strip() + "\n")
        stats = self._load("stats.json", {"user_messages": 0, "voice_at": None})
        stats["voice_at"] = stats["user_messages"]
        self._save("stats.json", stats)

    def add_voice_sample(self, text: str) -> None:
        path = self.dir / "voice_pack" / "samples.md"
        existing = path.read_text() if path.exists() else ""
        path.write_text(existing + f"\n---\n{text.strip()}\n")

    # skills kept loaded across turns
    def active_skills(self) -> list[str]:
        return self._load("active_skills.json", [])

    def activate_skills(self, names: list[str]) -> None:
        active = [s for s in self.active_skills() if s not in names] + names
        self._save("active_skills.json", active[-ACTIVE_SKILLS_LIMIT:])

    def clear_skills(self) -> None:
        self._save("active_skills.json", [])
