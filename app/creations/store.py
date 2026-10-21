"""Ateliê do Nym: guarda as criações com versões. Mesmo tipo + mesmo título = nova versão (a anterior fica no
histórico como 'superseded'). Nada aqui executa conteúdo: só grava e lê texto já validado em `sanitize.py`."""
from __future__ import annotations

import sqlite3
from typing import Any

from app.memory.text import normalize

KINDS = ("text", "drawing", "toy", "code")
KIND_ALIASES = {
    "text": "text", "texto": "text", "poema": "text", "historia": "text", "conto": "text",
    "drawing": "drawing", "desenho": "drawing", "svg": "drawing", "arte": "drawing",
    "toy": "toy", "brinquedo": "toy", "jogo": "toy", "animacao": "toy", "html": "toy",
    "code": "code", "codigo": "code", "python": "code", "script": "code",
}
KIND_LABEL = {"text": "texto", "drawing": "desenho", "toy": "brinquedo", "code": "código"}


def kind_of(value: str) -> str | None:
    return KIND_ALIASES.get(normalize(value).strip())


def _same_title(a: str, b: str) -> bool:
    return normalize(a).strip() == normalize(b).strip()


def current(conn: sqlite3.Connection, kind: str, title: str) -> sqlite3.Row | None:
    for row in conn.execute("SELECT * FROM creations WHERE kind = ? AND status = 'active' ORDER BY id DESC", (kind,)):
        if _same_title(row["title"], title):
            return row
    return None


def save(conn: sqlite3.Connection, kind: str, title: str, about: str | None, content: str, now: str,
         goal_id: int | None = None, task_id: int | None = None) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError("tipo de criação inválido")
    prev = current(conn, kind, title)
    version = prev["version"] + 1 if prev else 1
    if prev is not None:
        conn.execute("UPDATE creations SET status = 'superseded' WHERE id = ?", (prev["id"],))
    cur = conn.execute(
        "INSERT INTO creations (kind, title, about, content, version, parent_id, status, goal_id, task_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
        (kind, title, about, content, version, prev["id"] if prev else None, goal_id, task_id, now))
    return {"id": int(cur.lastrowid), "version": version}


def listing(conn: sqlite3.Connection, kind: str | None = None, include_archived: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    sql = "SELECT c.*, f.value AS reaction FROM creations c LEFT JOIN feedback f ON f.target = 'creation' AND f.target_id = c.id WHERE "
    sql += "c.status IN ('active', 'archived')" if include_archived else "c.status = 'active'"
    args: list[Any] = []
    if kind:
        sql += " AND c.kind = ?"
        args.append(kind)
    sql += " ORDER BY c.id DESC LIMIT ?"
    args.append(limit)
    out = []
    for r in conn.execute(sql, args):
        d = dict(r)
        d["chars"] = len(d.pop("content"))
        out.append(d)
    return out


def get(conn: sqlite3.Connection, creation_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT c.*, f.value AS reaction FROM creations c LEFT JOIN feedback f "
                       "ON f.target = 'creation' AND f.target_id = c.id WHERE c.id = ?", (creation_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["versions"] = [dict(v) for v in conn.execute(
        "WITH RECURSIVE chain(id) AS (SELECT ? UNION SELECT c.parent_id FROM creations c JOIN chain ON c.id = chain.id "
        "WHERE c.parent_id IS NOT NULL) SELECT id, version, created_at FROM creations WHERE id IN chain ORDER BY version",
        (creation_id,))]
    return d


def archive(conn: sqlite3.Connection, creation_id: int) -> bool:
    return conn.execute("UPDATE creations SET status = 'archived' WHERE id = ? AND status = 'active'",
                        (creation_id,)).rowcount > 0


def recent_lines(conn: sqlite3.Connection, limit: int = 6) -> list[str]:
    """Para o chat e as vontades: o que ele criou por último e o que você achou."""
    lines = []
    for r in conn.execute(
            "SELECT c.id, c.kind, c.title, c.version, c.created_at, f.value AS reaction FROM creations c "
            "LEFT JOIN feedback f ON f.target = 'creation' AND f.target_id = c.id WHERE c.status = 'active' "
            "ORDER BY c.id DESC LIMIT ?", (limit,)):
        mood = {1: " (ele curtiu)", -1: " (ele não curtiu)"}.get(r["reaction"], "")
        ver = f" v{r['version']}" if r["version"] > 1 else ""
        lines.append(f"[criação {r['id']}] {KIND_LABEL[r['kind']]} \"{r['title']}\"{ver}{mood}")
    return lines
