"""Position tracking storage (SQLite). Mount a Railway Volume at /data to keep history."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing

from .. import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracked (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    domain TEXT NOT NULL,
    keyword TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (chat_id, domain, keyword)
);
CREATE TABLE IF NOT EXISTS positions (
    tracked_id INTEGER NOT NULL REFERENCES tracked(id) ON DELETE CASCADE,
    checked_at TEXT NOT NULL DEFAULT (datetime('now')),
    position INTEGER,            -- NULL = not in top 100
    url TEXT
);
CREATE INDEX IF NOT EXISTS idx_positions ON positions (tracked_id, checked_at);
CREATE TABLE IF NOT EXISTS usage (
    user_id INTEGER NOT NULL,
    command TEXT NOT NULL,
    used_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    with closing(_connect()) as conn, conn:
        conn.executescript(SCHEMA)


class TrackingError(Exception):
    pass


def add(chat_id: int, domain: str, keyword: str) -> int:
    with closing(_connect()) as conn, conn:
        count = conn.execute("SELECT COUNT(*) FROM tracked WHERE chat_id = ?", (chat_id,)).fetchone()[0]
        if count >= config.MAX_TRACKED_PER_CHAT:
            raise TrackingError(f"You can track up to {config.MAX_TRACKED_PER_CHAT} keywords. Remove one with /untrack.")
        try:
            cur = conn.execute("INSERT INTO tracked (chat_id, domain, keyword) VALUES (?, ?, ?)",
                               (chat_id, domain, keyword))
        except sqlite3.IntegrityError:
            raise TrackingError("You're already tracking that keyword for this domain.") from None
        return cur.lastrowid


def remove(chat_id: int, tracked_id: int) -> bool:
    with closing(_connect()) as conn, conn:
        cur = conn.execute("DELETE FROM tracked WHERE id = ? AND chat_id = ?", (tracked_id, chat_id))
        return cur.rowcount > 0


def record(tracked_id: int, position: int | None, url: str | None) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO positions (tracked_id, position, url) VALUES (?, ?, ?)",
                     (tracked_id, position, url))


_LIST_SQL = """
SELECT t.id, t.chat_id, t.domain, t.keyword,
       (SELECT position FROM positions p WHERE p.tracked_id = t.id ORDER BY checked_at DESC, rowid DESC LIMIT 1) AS last_pos,
       (SELECT position FROM positions p WHERE p.tracked_id = t.id ORDER BY checked_at DESC, rowid DESC LIMIT 1 OFFSET 1) AS prev_pos,
       (SELECT checked_at FROM positions p WHERE p.tracked_id = t.id ORDER BY checked_at DESC, rowid DESC LIMIT 1) AS last_checked,
       (SELECT COUNT(*) FROM positions p WHERE p.tracked_id = t.id) AS checks
FROM tracked t
"""


def list_for_chat(chat_id: int) -> list[sqlite3.Row]:
    with closing(_connect()) as conn:
        return conn.execute(_LIST_SQL + " WHERE t.chat_id = ? ORDER BY t.domain, t.keyword", (chat_id,)).fetchall()


def all_tracked() -> list[sqlite3.Row]:
    with closing(_connect()) as conn:
        return conn.execute(_LIST_SQL + " ORDER BY t.chat_id, t.domain").fetchall()


def log_usage(user_id: int, command: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO usage (user_id, command) VALUES (?, ?)", (user_id, command))


def stats() -> dict:
    with closing(_connect()) as conn:
        q = lambda sql: conn.execute(sql).fetchone()[0]
        return {
            "users": q("SELECT COUNT(DISTINCT user_id) FROM usage"),
            "users_7d": q("SELECT COUNT(DISTINCT user_id) FROM usage WHERE used_at >= datetime('now', '-7 days')"),
            "commands": q("SELECT COUNT(*) FROM usage"),
            "commands_24h": q("SELECT COUNT(*) FROM usage WHERE used_at >= datetime('now', '-1 day')"),
            "tracked": q("SELECT COUNT(*) FROM tracked"),
            "top": conn.execute(
                "SELECT command, COUNT(*) c FROM usage GROUP BY command ORDER BY c DESC LIMIT 8").fetchall(),
        }


def change_text(last: int | None, prev: int | None, checks: int) -> str:
    if checks == 0:
        return "not checked yet"
    now = f"#{last}" if last else "not in top 100"
    if checks < 2:
        return now
    if last == prev:
        return f"{now} (=)"
    if last and prev:
        diff = prev - last
        return f"{now} ({'⬆️ +' if diff > 0 else '⬇️ '}{diff})"
    return f"{now} ({'🆕 entered' if last else '❌ dropped out'})"
