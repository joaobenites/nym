"""Acesso a dados de conversas, mensagens e auditoria (Fase 1)."""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("nym.database")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def create_conversation(conn: sqlite3.Connection, title: str) -> str:
    cid = uuid.uuid4().hex
    now = utcnow()
    conn.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (cid, title, now, now),
    )
    return cid


def get_conversation(conn: sqlite3.Connection, conversation_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()


def list_conversations(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM conversations ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()


def add_message(
    conn: sqlite3.Connection,
    conversation_id: str,
    role: str,
    content: str,
    model: str | None = None,
    created_at: str | None = None,
    initiative: bool = False,
) -> int:
    now = created_at or utcnow()
    cur = conn.execute(
        "INSERT INTO messages (conversation_id, role, content, model, created_at, initiative) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (conversation_id, role, content, model, now, 1 if initiative else 0),
    )
    conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
    return int(cur.lastrowid)


def list_messages(conn: sqlite3.Connection, conversation_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, role, content, model, created_at, initiative FROM messages "
        "WHERE conversation_id = ? ORDER BY id",
        (conversation_id,),
    ).fetchall()


def recent_messages(conn: sqlite3.Connection, conversation_id: str, limit: int) -> list[sqlite3.Row]:
    """Últimas `limit` mensagens em ordem cronológica."""
    rows = conn.execute(
        "SELECT id, role, content, model, created_at, initiative FROM messages "
        "WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
    return list(reversed(rows))


def log_audit(conn: sqlite3.Connection, event: str, detail: dict[str, Any] | None = None) -> None:
    """Registra um evento de auditoria. Nunca propaga falhas: auditar não pode derrubar o fluxo."""
    try:
        conn.execute(
            "INSERT INTO audit_log (created_at, event, detail) VALUES (?, ?, ?)",
            (utcnow(), event, json.dumps(detail, ensure_ascii=False) if detail else None),
        )
    except sqlite3.Error:
        log.exception("Falha ao gravar auditoria: %s", event)


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, utcnow()),
    )


def messages_page(
    conn: sqlite3.Connection, conversation_id: str, limit: int, before_id: int | None = None
) -> list[sqlite3.Row]:
    """Até `limit` mensagens, da mais nova para a mais antiga, opcionalmente anteriores a `before_id`."""
    if before_id is None:
        return conn.execute(
            "SELECT id, role, content, model, created_at, initiative FROM messages "
            "WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
    return conn.execute(
        "SELECT id, role, content, model, created_at, initiative FROM messages "
        "WHERE conversation_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
        (conversation_id, before_id, limit),
    ).fetchall()
