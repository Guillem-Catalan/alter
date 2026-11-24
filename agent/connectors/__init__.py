"""Connectors: the common shape of every integration (social networks, Google Workspace, Granola...).

Each connector lives in `agent/connectors/<id>.py`, declares its capabilities honestly (what it can read and
do, and through which access), and provides tools that are only offered while the account is connected.
Acting always goes through official APIs; a browser session may only be used to read (ADR 0006).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from ..db import Database
from ..tools import Tool


@dataclass
class Capability:
    text: str                                  # "Read impressions and reactions of your posts"
    kind: Literal["read", "act"]
    access: Literal["api", "session"] = "api"


@dataclass
class Connector:
    id: str
    name: str
    auth: Literal["oauth2", "api_key", "local", "browser_session"]
    capabilities: list[Capability] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)  # what it cannot do, said plainly

    def tools(self) -> list[Tool]:
        return []

    def __post_init__(self):
        for cap in self.capabilities:
            if cap.kind == "act" and cap.access != "api":
                raise ValueError(f"{self.id}: actions must use the official API, never a browser session")


@dataclass
class Connection:
    id: str
    connector: str
    account: str
    access: str


class Connections:
    """Which accounts a user has connected. Metadata only: secrets live in the keychain."""

    def __init__(self, db: Database):
        self.db = db

    def add(self, uid: str, connector: str, account: str, access: str = "api") -> Connection:
        connection_id = f"{uid}:{connector}:{account}"
        self.db.execute(
            "INSERT OR REPLACE INTO connections (id, uid, connector, account, access, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (connection_id, uid, connector, account, access, datetime.now(timezone.utc).isoformat()),
        )
        return Connection(connection_id, connector, account, access)

    def remove(self, uid: str, connection_id: str) -> None:
        self.db.execute("DELETE FROM connections WHERE id=? AND uid=?", (connection_id, uid))

    def of(self, uid: str) -> list[Connection]:
        rows = self.db.all("SELECT * FROM connections WHERE uid=? ORDER BY connector", (uid,))
        return [Connection(r["id"], r["connector"], r["account"], r["access"]) for r in rows]


# Registered connectors, by id. Each connector module adds itself here.
CONNECTORS: dict[str, Connector] = {}


def register(connector: Connector) -> Connector:
    if connector.id in CONNECTORS:
        raise ValueError(f"duplicate connector {connector.id}")
    CONNECTORS[connector.id] = connector
    return connector
