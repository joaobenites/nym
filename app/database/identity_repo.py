"""Acesso a dados de identidade, histórico de mudanças e objetivos (Fase 2)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.database.repository import utcnow

_CHANGE_UPDATABLE = {"status", "entry_id", "action", "old_value", "decided_at"}


# --- entradas de identidade -------------------------------------------------

def insert_entry(conn: sqlite3.Connection, kind: str, value: str, confidence: float | None) -> int:
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO identity_entries (kind, value, status, confidence, created_at, updated_at) "
        "VALUES (?, ?, 'active', ?, ?, ?)",
        (kind, value, confidence, now, now),
    )
    return int(cur.lastrowid)


def get_entry(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM identity_entries WHERE id = ?", (entry_id,)).fetchone()


def list_entries(conn: sqlite3.Connection, status: str | None = "active") -> list[sqlite3.Row]:
    if status is None:
        return conn.execute("SELECT * FROM identity_entries ORDER BY id").fetchall()
    return conn.execute(
        "SELECT * FROM identity_entries WHERE status = ? ORDER BY id", (status,)
    ).fetchall()


def update_entry(conn: sqlite3.Connection, entry_id: int, value: str, confidence: float | None) -> None:
    conn.execute(
        "UPDATE identity_entries SET value = ?, confidence = ?, updated_at = ? WHERE id = ?",
        (value, confidence, utcnow(), entry_id),
    )


def archive_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    conn.execute(
        "UPDATE identity_entries SET status = 'archived', updated_at = ? WHERE id = ?",
        (utcnow(), entry_id),
    )


# --- histórico de mudanças ---------------------------------------------------

def insert_change(
    conn: sqlite3.Connection,
    *,
    entry_id: int | None,
    kind: str,
    action: str,
    old_value: str | None,
    new_value: str | None,
    justification: str,
    related_memory_ids: list[Any],
    confidence: float | None,
    origin: str,
    status: str,
    decided_at: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO identity_changes (entry_id, kind, action, old_value, new_value, justification, "
        "related_memory_ids, confidence, origin, status, created_at, decided_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (entry_id, kind, action, old_value, new_value, justification,
         json.dumps(related_memory_ids), confidence, origin, status, utcnow(), decided_at),
    )
    return int(cur.lastrowid)


def get_change(conn: sqlite3.Connection, change_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM identity_changes WHERE id = ?", (change_id,)).fetchone()


def list_changes(conn: sqlite3.Connection, status: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
    if status is None:
        return conn.execute(
            "SELECT * FROM identity_changes ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM identity_changes WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit)
    ).fetchall()


def update_change(conn: sqlite3.Connection, change_id: int, **fields: Any) -> None:
    unknown = set(fields) - _CHANGE_UPDATABLE
    if unknown:
        raise ValueError(f"Campos não atualizáveis: {sorted(unknown)}")
    if not fields:
        return
    assignments = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(
        f"UPDATE identity_changes SET {assignments} WHERE id = ?",  # noqa: S608 - nomes vêm da whitelist
        (*fields.values(), change_id),
    )


def count_changes(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM identity_changes").fetchone()[0])


def count_pending(conn: sqlite3.Connection) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM identity_changes WHERE status = 'proposed'"
    ).fetchone()[0])


# --- objetivos ----------------------------------------------------------------

def insert_goal(conn: sqlite3.Connection, description: str, origin: str, priority: int, status: str) -> int:
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO goals (description, origin, priority, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (description, origin, priority, status, now, now),
    )
    return int(cur.lastrowid)


def get_goal(conn: sqlite3.Connection, goal_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()


def list_goals(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    if status is None:
        return conn.execute("SELECT * FROM goals ORDER BY priority, id").fetchall()
    return conn.execute(
        "SELECT * FROM goals WHERE status = ? ORDER BY priority, id", (status,)
    ).fetchall()


def update_goal(
    conn: sqlite3.Connection, goal_id: int, *, status: str | None, priority: int | None, description: str | None
) -> None:
    current = get_goal(conn, goal_id)
    conn.execute(
        "UPDATE goals SET status = ?, priority = ?, description = ?, updated_at = ? WHERE id = ?",
        (status or current["status"], priority or current["priority"],
         description or current["description"], utcnow(), goal_id),
    )
