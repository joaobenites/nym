"""Projetos do Nym (Fase 9): coisas que duram dias, feitas de vários objetivos em sequência.

Ex.: "minha coleção de criaturas de dados" (desenhar uma, pesquisar referências, melhorar a anterior...). O projeto
dá continuidade: a cada vontade ele vê o que já fez no projeto e escolhe o próximo passo, em vez de recomeçar do zero
ou repetir o mesmo objetivo. Quem abre e fecha projetos é ele (pelas vontades, validado pelo código); você pode
largar um projeto pela página Vida.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.memory.text import is_duplicate, jaccard, looks_sensitive, token_set

MAX_ACTIVE = 3
MAX_TITLE = 120
MAX_WHY = 400


def active(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    for p in conn.execute("SELECT * FROM projects WHERE status = 'active' ORDER BY id"):
        d = dict(p)
        d["goals"] = [dict(g) for g in conn.execute(
            "SELECT id, description, status, substr(COALESCE(result, last_error, ''), 1, 160) AS note FROM goals "
            "WHERE project_id = ? ORDER BY id", (p["id"],))]
        out.append(d)
    return out


def clean_new(raw: Any, existing: list[dict[str, Any]]) -> dict[str, str] | None:
    """Valida um projeto novo proposto pelo modelo. None = descartar."""
    if not isinstance(raw, dict):
        return None
    title = " ".join(str(raw.get("title") or "").split())[:MAX_TITLE]
    why = " ".join(str(raw.get("why") or "").split())[:MAX_WHY]
    if len(title) < 5 or not why or looks_sensitive(title + " " + why):
        return None
    for p in existing:
        if is_duplicate(title, p["title"], threshold=0.6) or jaccard(token_set(title), token_set(p["title"])) >= 0.5:
            return None
    return {"title": title, "why": why}


def create(conn: sqlite3.Connection, title: str, why: str, refs: dict[str, Any], now: str) -> int:
    cur = conn.execute("INSERT INTO projects (title, why, status, refs, created_at, updated_at) VALUES (?, ?, 'active', ?, ?, ?)",
                       (title, why, json.dumps(refs, ensure_ascii=False), now, now))
    return int(cur.lastrowid)


def close(conn: sqlite3.Connection, project_id: int, status: str, note: str | None, now: str) -> bool:
    if status not in ("done", "dropped"):
        raise ValueError("status inválido")
    return conn.execute("UPDATE projects SET status = ?, closing_note = ?, updated_at = ? WHERE id = ? AND status = 'active'",
                        (status, (note or "")[:400] or None, now, project_id)).rowcount > 0


def touch(conn: sqlite3.Connection, project_id: int, now: str) -> None:
    conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))


def block(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    status = {"done": "feito", "paused": "travou", "active": "em andamento", "cancelled": "cancelado", "proposed": "proposto"}
    lines = []
    for p in items:
        lines.append(f"- [projeto {p['id']}] {p['title']} — por quê: {p['why']}")
        for g in p["goals"][-6:]:
            lines.append(f"    · {g['description']} ({status.get(g['status'], g['status'])})" + (f": {g['note']}" if g["note"] else ""))
    return "\n".join(lines)


def listing(conn: sqlite3.Connection, limit: int = 30) -> list[dict[str, Any]]:
    out = []
    for p in conn.execute("SELECT * FROM projects ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, id DESC LIMIT ?", (limit,)):
        d = dict(p)
        d["refs"] = json.loads(d["refs"] or "{}")
        d["goals"] = [dict(g) for g in conn.execute(
            "SELECT id, description, status, created_at FROM goals WHERE project_id = ? ORDER BY id", (p["id"],))]
        out.append(d)
    return out
