"""Armazenamento de memórias (SQL). Todas as funções recebem a conexão e NÃO abrem transação:
quem chama decide o escopo (para que várias operações possam ser atômicas).

Política de retenção (exclusão):
- `delete_memory` remove a memória, suas fontes, ligações e histórico (cascata), e grava
  apenas uma lápide sem conteúdo no audit_log.
- Com `purge_sources=True`, apaga também as mensagens de origem e toda memória que cite
  alguma delas (inclusive o resumo do episódio, que pode conter a informação).
- Sem `purge_sources`, as mensagens do chat e o resumo do episódio permanecem.
- Em ambos os casos, itens de sonho, tarefas e assuntos que citam a memória apagada também são removidos.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, Sequence

from app.database.repository import log_audit, utcnow
from app.memory.text import is_duplicate, is_superset, token_set

KINDS = ("episodic", "semantic", "identity")
EPISTEMICS = ("confirmed", "sourced", "hypothesis", "unverified")
ORIGINS = ("user_stated", "nym_inferred", "external_source", "user_edit", "system")
STATUSES = ("active", "archived", "superseded")
RELATIONS = ("part_of", "follows", "supports", "contradicts", "related", "duplicate_of", "supersedes")
ACTORS = ("user", "system", "nym")
MAX_CONTENT_CHARS = 1000


class MemoryRuleError(Exception):
    """Erro de regra de negócio da memória."""


class InvalidMemoryInput(MemoryRuleError):
    pass


class MemoryNotFound(MemoryRuleError):
    pass


def _clamp01(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise InvalidMemoryInput(f"{name} deve ser um número entre 0 e 1.")
    return float(value)


def _check_content(content: Any) -> str:
    if not isinstance(content, str) or not content.strip():
        raise InvalidMemoryInput("O conteúdo não pode ser vazio.")
    content = content.strip()
    if len(content) > MAX_CONTENT_CHARS:
        raise InvalidMemoryInput(f"O conteúdo excede {MAX_CONTENT_CHARS} caracteres.")
    return content


def add_history(
    conn: sqlite3.Connection, memory_id: int, action: str, old: str | None, new: str | None,
    actor: str, reason: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO memory_history (memory_id, action, old_value, new_value, actor, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (memory_id, action, old, new, actor, reason, utcnow()),
    )


def add_source(
    conn: sqlite3.Connection, memory_id: int, source_type: str,
    message_id: int | None = None, ref: str | None = None,
) -> None:
    if message_id is not None and conn.execute(
        "SELECT 1 FROM memory_sources WHERE memory_id = ? AND message_id = ?", (memory_id, message_id)
    ).fetchone():
        return
    conn.execute(
        "INSERT INTO memory_sources (memory_id, source_type, message_id, ref, created_at) VALUES (?, ?, ?, ?, ?)",
        (memory_id, source_type, message_id, ref, utcnow()),
    )


def add_link(conn: sqlite3.Connection, from_id: int, to_id: int, relation: str) -> None:
    if relation not in RELATIONS:
        raise InvalidMemoryInput(f"Relação inválida: {relation!r}.")
    if from_id == to_id:
        return
    conn.execute(
        "INSERT OR IGNORE INTO memory_links (from_id, to_id, relation, created_at) VALUES (?, ?, ?, ?)",
        (from_id, to_id, relation, utcnow()),
    )


def create_memory(
    conn: sqlite3.Connection,
    *,
    kind: str,
    content: str,
    epistemic: str | None,
    origin: str,
    importance: float = 0.5,
    confidence: float = 0.5,
    entities: Sequence[str] | None = None,
    occurred_at: str | None = None,
    ended_at: str | None = None,
    outcome: str | None = None,
    message_ids: Iterable[int] = (),
    manual: bool = False,
    actor: str = "system",
    reason: str | None = None,
) -> int:
    if kind not in KINDS:
        raise InvalidMemoryInput(f"Tipo inválido: {kind!r}.")
    if origin not in ORIGINS:
        raise InvalidMemoryInput(f"Origem inválida: {origin!r}.")
    if kind == "episodic":
        epistemic = None
    elif epistemic not in EPISTEMICS:
        raise InvalidMemoryInput(f"Estado epistêmico inválido: {epistemic!r}.")
    content = _check_content(content)
    importance = _clamp01(importance, "importance")
    confidence = _clamp01(confidence, "confidence")
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO memories (kind, content, outcome, epistemic, origin, importance, confidence, "
        "recurrence, status, entities, occurred_at, ended_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'active', ?, ?, ?, ?, ?)",
        (kind, content, outcome, epistemic, origin, importance, confidence,
         json.dumps(list(entities or []), ensure_ascii=False), occurred_at or now, ended_at, now, now),
    )
    memory_id = int(cur.lastrowid)
    for message_id in message_ids:
        add_source(conn, memory_id, "message", message_id=message_id)
    if manual:
        add_source(conn, memory_id, "manual", ref="criada pelo usuário")
    add_history(conn, memory_id, "created", None, content, actor, reason)
    return memory_id


def get_memory(conn: sqlite3.Connection, memory_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()


def require_memory(conn: sqlite3.Connection, memory_id: int) -> sqlite3.Row:
    row = get_memory(conn, memory_id) if isinstance(memory_id, int) else None
    if row is None:
        raise MemoryNotFound("Memória não encontrada.")
    return row


def list_memories(
    conn: sqlite3.Connection, *, kind: str | None = None, epistemic: str | None = None,
    status: str | None = "active", q: str | None = None, limit: int = 100, offset: int = 0,
) -> list[sqlite3.Row]:
    clauses, params = [], []
    for column, value, allowed in (("kind", kind, KINDS), ("epistemic", epistemic, EPISTEMICS),
                                   ("status", status, STATUSES)):
        if value is not None:
            if value not in allowed:
                raise InvalidMemoryInput(f"Filtro inválido em {column}: {value!r}.")
            clauses.append(f"{column} = ?")
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM memories {where} ORDER BY occurred_at DESC, id DESC",  # noqa: S608 - colunas fixas
        params,
    ).fetchall()
    if q and q.strip():
        wanted = token_set(q)
        rows = [r for r in rows if wanted & token_set(r["content"])]
    return rows[offset: offset + max(1, min(limit, 500))]


def sources_of(conn: sqlite3.Connection, memory_id: int) -> list[dict[str, Any]]:
    out = []
    for r in conn.execute("SELECT * FROM memory_sources WHERE memory_id = ? ORDER BY id", (memory_id,)):
        item = dict(r)
        if r["message_id"] is not None:
            msg = conn.execute("SELECT role, content, created_at FROM messages WHERE id = ?",
                               (r["message_id"],)).fetchone()
            item["message"] = dict(msg) if msg else None
        out.append(item)
    return out


def links_of(conn: sqlite3.Connection, memory_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT from_id, to_id, relation FROM memory_links WHERE from_id = ? OR to_id = ? ORDER BY id",
        (memory_id, memory_id),
    ).fetchall()
    return [dict(r) for r in rows]


def history_of(conn: sqlite3.Connection, memory_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM memory_history WHERE memory_id = ? ORDER BY id", (memory_id,))]


def update_memory(
    conn: sqlite3.Connection, memory_id: int, *, content: str | None = None,
    epistemic: str | None = None, importance: float | None = None, actor: str = "user",
    reason: str | None = None,
) -> sqlite3.Row:
    row = require_memory(conn, memory_id)
    now = utcnow()
    if content is not None:
        new_content = _check_content(content)
        if new_content != row["content"]:
            conn.execute("UPDATE memories SET content = ?, updated_at = ? WHERE id = ?",
                         (new_content, now, memory_id))
            add_history(conn, memory_id, "edited", row["content"], new_content, actor, reason)
            if actor == "user" and epistemic is None and row["kind"] != "episodic":
                epistemic = "confirmed"  # o usuário corrigiu: a versão dele passa a valer como confirmada
    if epistemic is not None:
        if row["kind"] == "episodic":
            raise InvalidMemoryInput("Episódios não têm estado epistêmico.")
        if epistemic not in EPISTEMICS:
            raise InvalidMemoryInput(f"Estado epistêmico inválido: {epistemic!r}.")
        if epistemic != row["epistemic"]:
            conn.execute("UPDATE memories SET epistemic = ?, updated_at = ? WHERE id = ?",
                         (epistemic, now, memory_id))
            add_history(conn, memory_id, "epistemic_changed", row["epistemic"], epistemic, actor, reason)
            if actor == "user":
                conn.execute("UPDATE memories SET origin = 'user_edit' WHERE id = ?", (memory_id,))
    if importance is not None:
        conn.execute("UPDATE memories SET importance = ?, updated_at = ? WHERE id = ?",
                     (_clamp01(importance, "importance"), now, memory_id))
    return require_memory(conn, memory_id)


def set_status(conn: sqlite3.Connection, memory_id: int, status: str, actor: str, reason: str | None = None) -> None:
    row = require_memory(conn, memory_id)
    if status not in ("active", "archived"):
        raise InvalidMemoryInput("Use 'active' ou 'archived'.")
    if row["status"] == status:
        return
    conn.execute("UPDATE memories SET status = ?, superseded_by = NULL, updated_at = ? WHERE id = ?",
                 (status, utcnow(), memory_id))
    add_history(conn, memory_id, "archived" if status == "archived" else "restored", None, None, actor, reason)


def bump_recurrence(
    conn: sqlite3.Connection, memory_id: int, *, confidence: float | None, message_ids: Iterable[int],
    actor: str = "system", reason: str | None = None,
) -> None:
    row = require_memory(conn, memory_id)
    new_conf = max(row["confidence"], confidence) if confidence is not None else row["confidence"]
    conn.execute("UPDATE memories SET recurrence = recurrence + 1, confidence = ?, updated_at = ? WHERE id = ?",
                 (new_conf, utcnow(), memory_id))
    for message_id in message_ids:
        add_source(conn, memory_id, "message", message_id=message_id)
    add_history(conn, memory_id, "recurrence", None, None, actor, reason or "mencionada novamente")


def supersede(conn: sqlite3.Connection, old_id: int, new_id: int, actor: str, reason: str) -> None:
    conn.execute("UPDATE memories SET status = 'superseded', superseded_by = ?, updated_at = ? WHERE id = ?",
                 (new_id, utcnow(), old_id))
    add_link(conn, new_id, old_id, "supersedes")
    add_history(conn, old_id, "superseded", None, None, actor, reason)


def find_similar(conn: sqlite3.Connection, content: str, kind: str, exclude_id: int | None = None) -> sqlite3.Row | None:
    for row in conn.execute("SELECT * FROM memories WHERE kind = ? AND status = 'active' ORDER BY id", (kind,)):
        if row["id"] != exclude_id and is_duplicate(content, row["content"]):
            return row
    return None


def touch_accessed(conn: sqlite3.Connection, memory_ids: Iterable[int]) -> None:
    now = utcnow()
    for memory_id in memory_ids:
        conn.execute("UPDATE memories SET last_accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
                     (now, memory_id))


def merge_into(conn: sqlite3.Connection, keep_id: int, drop_id: int, actor: str = "system") -> None:
    """Funde `drop_id` em `keep_id` sem perder rastreabilidade (fontes, ligações e recorrência)."""
    keep, drop = require_memory(conn, keep_id), require_memory(conn, drop_id)
    for src in conn.execute("SELECT * FROM memory_sources WHERE memory_id = ?", (drop_id,)).fetchall():
        add_source(conn, keep_id, src["source_type"], message_id=src["message_id"], ref=src["ref"])
    for link in conn.execute("SELECT * FROM memory_links WHERE from_id = ? OR to_id = ?", (drop_id, drop_id)).fetchall():
        if link["relation"] in ("duplicate_of", "supersedes"):
            continue
        a = keep_id if link["from_id"] == drop_id else link["from_id"]
        b = keep_id if link["to_id"] == drop_id else link["to_id"]
        add_link(conn, a, b, link["relation"])
    new_content = keep["content"]
    if is_superset(drop["content"], keep["content"]):  # o duplicado traz informação a mais
        new_content = drop["content"]
        conn.execute("UPDATE memories SET content = ? WHERE id = ?", (new_content, keep_id))
        add_history(conn, keep_id, "edited", keep["content"], new_content, actor, f"mesclada com #{drop_id}: texto mais completo")
    conn.execute(
        "UPDATE memories SET recurrence = recurrence + ?, confidence = ?, importance = ?, updated_at = ? WHERE id = ?",
        (drop["recurrence"], max(keep["confidence"], drop["confidence"]),
         max(keep["importance"], drop["importance"]), utcnow(), keep_id),
    )
    add_history(conn, keep_id, "merged", None, None, actor, f"mesclada com #{drop_id}")  # sem conteúdo alheio
    conn.execute("UPDATE memories SET status = 'superseded', superseded_by = ?, updated_at = ? WHERE id = ?",
                 (keep_id, utcnow(), drop_id))
    add_link(conn, drop_id, keep_id, "duplicate_of")
    add_history(conn, drop_id, "superseded", None, None, actor, f"duplicata de #{keep_id}")


def dedupe_batch(conn: sqlite3.Connection, kinds: Sequence[str] = ("semantic", "identity"), dry_run: bool = True) -> list[dict[str, int]]:
    """Acha (e, se não for dry_run, funde) memórias duplicadas. Mantém a mais antiga."""
    merges: list[dict[str, int]] = []
    for kind in kinds:
        rows = conn.execute("SELECT id, content FROM memories WHERE kind = ? AND status = 'active' ORDER BY id", (kind,)).fetchall()
        gone: set[int] = set()
        for i, a in enumerate(rows):
            if a["id"] in gone:
                continue
            for b in rows[i + 1:]:
                if b["id"] in gone or not is_duplicate(a["content"], b["content"]):
                    continue
                merges.append({"keep": a["id"], "drop": b["id"]})
                gone.add(b["id"])
                if not dry_run:
                    merge_into(conn, a["id"], b["id"])
    return merges


def delete_memory(conn: sqlite3.Connection, memory_id: int, purge_sources: bool = False) -> dict[str, Any]:
    """Exclusão de verdade (ver política no topo do módulo). Deve rodar dentro de uma transação."""
    row = require_memory(conn, memory_id)
    message_ids = {r["message_id"] for r in conn.execute(
        "SELECT message_id FROM memory_sources WHERE memory_id = ? AND message_id IS NOT NULL", (memory_id,))}
    to_delete = {memory_id}
    deleted_messages = 0
    if purge_sources and message_ids:
        marks = ",".join("?" * len(message_ids))
        for r in conn.execute(f"SELECT DISTINCT memory_id FROM memory_sources WHERE message_id IN ({marks})",  # noqa: S608
                              tuple(message_ids)):
            to_delete.add(r["memory_id"])
    for mid in sorted(to_delete):
        conn.execute("UPDATE memories SET superseded_by = NULL, status = 'archived' WHERE superseded_by = ?", (mid,))
        conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
    if purge_sources and message_ids:
        marks = ",".join("?" * len(message_ids))
        conn.execute(f"DELETE FROM memory_sources WHERE message_id IN ({marks})", tuple(message_ids))  # noqa: S608
        deleted_messages = conn.execute(f"DELETE FROM messages WHERE id IN ({marks})", tuple(message_ids)).rowcount  # noqa: S608
    purge_dream_traces(conn, to_delete)
    log_audit(conn, "memory.deleted", {  # lápide: sem nenhum conteúdo
        "memory_id": memory_id, "kind": row["kind"], "purge_sources": purge_sources,
        "memories_deleted": len(to_delete), "messages_deleted": deleted_messages,
    })
    return {"memories_deleted": sorted(to_delete), "messages_deleted": deleted_messages}


def purge_dream_traces(conn: sqlite3.Connection, memory_ids: Iterable[int]) -> None:
    """Remove dos sonhos tudo o que cita as memórias apagadas (itens, tarefas, assuntos e o resumo do ciclo)."""
    ids = list(memory_ids)
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    for table in ("dream_items", "exploration_tasks", "pending_messages"):
        conn.execute(
            f"DELETE FROM {table} WHERE EXISTS "  # noqa: S608 - nomes fixos
            f"(SELECT 1 FROM json_each({table}.refs) WHERE value IN ({marks}))", ids)
    conn.execute(
        f"UPDATE dream_cycles SET review = '[removido: uma lembrança usada neste ciclo foi apagada]' "  # noqa: S608
        f"WHERE EXISTS (SELECT 1 FROM json_each(dream_cycles.selected_ids) WHERE value IN ({marks}))", ids)


def episode_count(conn: sqlite3.Connection, memory_id: int) -> int:
    return int(conn.execute(
        "SELECT COUNT(DISTINCT to_id) FROM memory_links WHERE from_id = ? AND relation = 'part_of'",
        (memory_id,)).fetchone()[0])


def recurring_identity_candidates(conn: sqlite3.Connection, min_recurrence: int = 3, min_episodes: int = 2) -> list[dict[str, Any]]:
    """Observações que se repetem em episódios diferentes: base para propostas de identidade (Fase 4).
    Uma observação isolada nunca basta."""
    out = []
    for r in conn.execute("SELECT * FROM memories WHERE kind = 'identity' AND status = 'active' AND recurrence >= ?",
                          (min_recurrence,)):
        episodes = episode_count(conn, r["id"])
        if episodes >= min_episodes:
            out.append({**dict(r), "episodes": episodes})
    return out
