"""Tarefas de exploração derivadas dos sonhos.

Nesta fase elas só são REGISTRADAS, com limites. Executá-las (pesquisa, ferramentas, permissões) é das Fases 5 e 6:
hoje o usuário pode aprová-las ("pronta para quando houver ferramentas") ou dispensá-las. O resultado de uma
exploração futura entrará como evidência nova, separada da hipótese que a originou.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Sequence

from app.database.repository import utcnow
from app.memory.text import is_duplicate, jaccard, token_set

STATUSES = ("pending", "approved", "dismissed", "done", "expired")
MAX_PENDING = 10
REPEAT_OVERLAP = 0.4


def add_task(
    conn: sqlite3.Connection, cycle_id: int, question: str, rationale: str, refs: Sequence[int],
    max_new: int, new_so_far: int,
) -> int | None:
    """Cria a tarefa se couber nos limites e não for repetida. Retorna o id ou None."""
    if new_so_far >= max_new:
        return None
    active = conn.execute("SELECT question FROM exploration_tasks WHERE status IN ('pending', 'approved')").fetchall()
    if len(active) >= MAX_PENDING:
        return None
    # Repetida: igual a uma aberta, ou com muitas palavras em comum com qualquer uma das últimas (inclusive já feitas).
    recent = conn.execute("SELECT question FROM exploration_tasks ORDER BY id DESC LIMIT 60").fetchall()
    words = token_set(question)
    if any(is_duplicate(question, r["question"]) or jaccard(words, token_set(r["question"])) >= REPEAT_OVERLAP for r in recent):
        return None
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO exploration_tasks (cycle_id, question, rationale, refs, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?, ?)", (cycle_id, question, rationale or None, json.dumps(list(refs)), now, now))
    return int(cur.lastrowid)


def list_tasks(conn: sqlite3.Connection, status: str | None = None) -> list[dict[str, Any]]:
    if status is not None and status not in STATUSES:
        raise ValueError(f"Estado inválido: {status!r}.")
    rows = conn.execute("SELECT * FROM exploration_tasks" + (" WHERE status = ?" if status else "") + " ORDER BY id DESC",
                        (status,) if status else ()).fetchall()
    return [{**dict(r), "refs": json.loads(r["refs"])} for r in rows]


def set_status(conn: sqlite3.Connection, task_id: int, status: str) -> bool:
    if status not in ("approved", "dismissed"):
        raise ValueError("O usuário só pode aprovar ou dispensar.")
    cur = conn.execute("UPDATE exploration_tasks SET status = ?, updated_at = ? WHERE id = ? AND status IN ('pending', 'approved')",
                       (status, utcnow(), task_id))
    return cur.rowcount > 0
