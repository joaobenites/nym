"""Reações do usuário (👍/👎 + comentário) e lições de conversa tiradas delas (Fase 7).

- Reagir a uma resposta dá ou tira ponto das lições de conversa que estavam ativas quando ela foi escrita.
- De tempos em tempos (com o chat parado), as reações novas viram até 2 lições de conversa: o modelo propõe,
  citando os ids das reações; o código confere os ids, descarta o sensível e o repetido (reforça em vez disso).
- Reagir a uma criação entra nas vontades dele ("ele curtiu isso"), para ele fazer mais do que você gosta.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Sequence

from app.learning import lessons
from app.models.base import ChatMessage

MAX_COMMENT_CHARS = 300
BATCH = 8
MIN_TO_LEARN = 3

PROMPT = """Você é o Nym aprendendo com as reações do usuário às SUAS respostas. Abaixo, respostas suas com a
reação dele (gostou / não gostou) e, às vezes, um comentário. Tire no máximo 2 lições sobre COMO conversar com ele
(tom, tamanho, assunto, jeito de perguntar). Responda SOMENTE com JSON:
{"lessons": [{"text": "lição em uma frase, em segunda pessoa (ex.: 'Quando ele conta do trabalho, pergunte mais e opine menos')",
  "feedback": [ids das reações que mostram isso]}]}
Regras: só o que as reações mostram de verdade; nada genérico ("seja educado"); lista vazia se não houver padrão."""


class FeedbackError(ValueError):
    pass


def record(conn: sqlite3.Connection, target: str, target_id: int, value: int, comment: str | None, now: str) -> dict[str, Any] | None:
    if target not in ("message", "creation"):
        raise FeedbackError("Alvo inválido.")
    if value not in (-1, 0, 1):
        raise FeedbackError("Reação inválida: use 1, -1 ou 0 (tirar).")
    if target == "message":
        row = conn.execute("SELECT role FROM messages WHERE id = ?", (target_id,)).fetchone()
        if row is None or row["role"] != "assistant":
            raise FeedbackError("Só dá para reagir a mensagens do Nym.")
    elif conn.execute("SELECT 1 FROM creations WHERE id = ?", (target_id,)).fetchone() is None:
        raise FeedbackError("Criação não encontrada.")
    comment = " ".join(comment.split())[:MAX_COMMENT_CHARS] if isinstance(comment, str) and comment.strip() else None
    prev = conn.execute("SELECT * FROM feedback WHERE target = ? AND target_id = ?", (target, target_id)).fetchone()
    if value == 0:
        conn.execute("DELETE FROM feedback WHERE target = ? AND target_id = ?", (target, target_id))
        return None
    if prev is None:
        conn.execute("INSERT INTO feedback (target, target_id, value, comment, created_at) VALUES (?, ?, ?, ?, ?)",
                     (target, target_id, value, comment, now))
    else:
        conn.execute("UPDATE feedback SET value = ?, comment = ?, learned = CASE WHEN value = ? THEN learned ELSE 0 END, "
                     "created_at = ? WHERE id = ?", (value, comment, value, now, prev["id"]))
    if target == "message":
        if prev is None:
            lessons.settle(conn, "message", target_id, value > 0, now)
        else:
            lessons.change_outcome(conn, "message", target_id, value > 0, now)
    return dict(conn.execute("SELECT * FROM feedback WHERE target = ? AND target_id = ?", (target, target_id)).fetchone())


def for_messages(conn: sqlite3.Connection, ids: Sequence[int]) -> dict[int, int]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {r["target_id"]: r["value"] for r in conn.execute(
        f"SELECT target_id, value FROM feedback WHERE target = 'message' AND target_id IN ({marks})", list(ids))}  # noqa: S608


def unlearned(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT f.id, f.value, f.comment, m.id AS message_id, m.content, m.conversation_id FROM feedback f "
        "JOIN messages m ON m.id = f.target_id WHERE f.target = 'message' AND f.learned = 0 ORDER BY f.id LIMIT ?",
        (BATCH,)).fetchall()
    out = []
    for r in rows:
        before = conn.execute("SELECT content FROM messages WHERE conversation_id = ? AND id < ? AND role = 'user' "
                              "ORDER BY id DESC LIMIT 1", (r["conversation_id"], r["message_id"])).fetchone()
        out.append({**dict(r), "user_before": before["content"] if before else ""})
    return out


def ready(items: Sequence[dict[str, Any]]) -> bool:
    return len(items) >= MIN_TO_LEARN or any(i["comment"] for i in items)


def build_messages(items: Sequence[dict[str, Any]]) -> list[ChatMessage]:
    lines = []
    for i in items:
        mood = "GOSTOU" if i["value"] > 0 else "NÃO GOSTOU"
        lines.append(f"[{i['id']}] {mood}" + (f" — comentário dele: {i['comment']}" if i["comment"] else "")
                     + f"\nEle tinha dito: {i['user_before'][:300]}\nVocê respondeu: {i['content'][:500]}")
    return [ChatMessage("system", PROMPT), ChatMessage("user", "\n\n".join(lines))]


def apply_learning(conn: sqlite3.Connection, items: Sequence[dict[str, Any]], data: Any, now: str) -> list[int]:
    """Valida as lições propostas e marca as reações como usadas (mesmo sem lição: não reprocessa)."""
    allowed = {i["id"] for i in items}
    saved: list[int] = []
    raw = data.get("lessons") if isinstance(data, dict) else None
    for entry in (raw if isinstance(raw, list) else [])[:2]:
        if not isinstance(entry, dict):
            continue
        text = lessons.clean_text(entry.get("text"))
        ids = [x for x in entry.get("feedback") or [] if isinstance(x, int) and x in allowed] \
            if isinstance(entry.get("feedback"), list) else []
        if not text or not ids:
            continue
        saved.append(lessons.add(conn, "chat", text, [], {"feedback": ids}, now)["id"])
    marks = ",".join("?" * len(allowed))
    if allowed:
        conn.execute(f"UPDATE feedback SET learned = 1 WHERE id IN ({marks})", list(allowed))  # noqa: S608
    return saved
