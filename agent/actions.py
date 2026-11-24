"""Pending actions and confirmation cards (ADR 0002).

An act tool never runs when the model calls it. It becomes a pending action; the user confirms or cancels it
from their own message (a button or typing yes). Asking for a change makes the model call the tool again with
`replaces`, which withdraws the old card. Every outcome is written to the audit log.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .db import Database
from .tools import Card, Tool, ToolResult

CARD_TTL = timedelta(hours=24)


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Pending:
    id: str
    uid: str
    tool: str
    input: dict
    summary: str
    content: str
    image: str
    status: str
    expires_at: datetime

    @property
    def card(self) -> Card:
        return Card(self.summary, self.content, self.image)


class Actions:
    def __init__(self, db: Database):
        self.db = db

    def _row(self, row) -> Pending:
        return Pending(row["id"], row["uid"], row["tool"], json.loads(row["input"]), row["summary"], row["content"],
                       row["image"], row["status"], datetime.fromisoformat(row["expires_at"]))

    def _expire(self, uid: str) -> None:
        self.db.execute("UPDATE pending_actions SET status='expired', decided_at=? "
                        "WHERE uid=? AND status='pending' AND expires_at < ?",
                        (now().isoformat(), uid, now().isoformat()))

    def create(self, uid: str, tool: Tool, args: dict, ctx) -> Pending:
        """Store a pending action and withdraw the cards it replaces (only this user's pending ones)."""
        replaces = [str(r) for r in args.pop("replaces", []) or []]
        card = tool.card(ctx, args) if tool.card else Card(tool.name)
        action_id = uuid.uuid4().hex[:8]
        created = now()
        self.db.execute(
            "INSERT INTO pending_actions (id, uid, tool, input, summary, content, image, status, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (action_id, uid, tool.name, json.dumps(args, ensure_ascii=False), card.summary, card.content, card.image,
             created.isoformat(), (created + CARD_TTL).isoformat()),
        )
        for old in replaces:
            self.db.execute("UPDATE pending_actions SET status='superseded', decided_at=? "
                            "WHERE id=? AND uid=? AND status='pending'", (now().isoformat(), old, uid))
        return self.get(uid, action_id)  # type: ignore[return-value]

    def get(self, uid: str, action_id: str) -> Pending | None:
        self._expire(uid)
        row = self.db.one("SELECT * FROM pending_actions WHERE id=? AND uid=?", (action_id, uid))
        return self._row(row) if row else None

    def pending(self, uid: str) -> list[Pending]:
        self._expire(uid)
        rows = self.db.all("SELECT * FROM pending_actions WHERE uid=? AND status='pending' ORDER BY created_at", (uid,))
        return [self._row(r) for r in rows]

    def cancel(self, uid: str, action_id: str) -> Pending | None:
        action = self.get(uid, action_id)
        if not action or action.status != "pending":
            return None
        self.db.execute("UPDATE pending_actions SET status='cancelled', decided_at=? WHERE id=?",
                        (now().isoformat(), action_id))
        self._audit(action, confirmed_by="", outcome="cancelled")
        return action

    def confirm(self, uid: str, action_id: str, tool: Tool | None, ctx, confirmed_by: str) -> tuple[Pending | None, ToolResult]:
        """Run a confirmed action. Only callable from a user's own message, never from a model tool call."""
        action = self.get(uid, action_id)
        if not action:
            return None, ToolResult("That card does not exist.", is_error=True)
        if action.status != "pending":
            return action, ToolResult(f"That card is {action.status}; nothing was done.", is_error=True)
        if tool is None:
            return action, ToolResult(f"The tool {action.tool} is no longer available.", is_error=True)
        # Claim the action first so a double tap cannot run it twice.
        claimed = self.db.execute("UPDATE pending_actions SET status='running', decided_at=? WHERE id=? AND status='pending'",
                                  (now().isoformat(), action_id)).rowcount
        if not claimed:
            return action, ToolResult("That card was already handled.", is_error=True)
        try:
            raw = tool.run(ctx, dict(action.input))
            result = raw if isinstance(raw, ToolResult) else ToolResult(str(raw))
        except Exception as e:  # the outcome must always be recorded
            result = ToolResult(f"{type(e).__name__}: {e}", is_error=True)
        status = "failed" if result.is_error else "done"
        self.db.execute("UPDATE pending_actions SET status=?, result=? WHERE id=?", (status, result.content, action_id))
        self._audit(action, confirmed_by=confirmed_by, outcome=status, detail=result.content)
        return action, result

    def _audit(self, action: Pending, confirmed_by: str, outcome: str, detail: str = "") -> None:
        self.db.execute(
            "INSERT INTO audit_log (at, uid, action_id, tool, input, confirmed_by, outcome, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now().isoformat(), action.uid, action.id, action.tool, json.dumps(action.input, ensure_ascii=False),
             confirmed_by, outcome, detail[:2000]),
        )

    def audit(self, uid: str, limit: int = 50) -> list[dict]:
        rows = self.db.all("SELECT * FROM audit_log WHERE uid=? ORDER BY id DESC LIMIT ?", (uid, limit))
        return [dict(r) for r in rows]
