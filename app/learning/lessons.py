"""Aprendizado por experiência (Fase 7): o Nym muda o JEITO de agir com base no que deu certo e errado.

Não é só guardar fatos. Três mecanismos, todos com registro e placar reais:
1. Lições de tarefa: ao avaliar um objetivo, o modelo pode tirar UMA lição de como fazer ("buscar em inglês achou
   mais fontes"), com os passos que mostram isso. Lições entram no planejamento de objetivos parecidos. Quando o
   objetivo que usou a lição termina bem, ela ganha ponto; quando dá errado, perde. Lição que falha mais do que ajuda
   é aposentada sozinha. A mesma lição tirada de novo, de outra experiência, é reforçada em vez de duplicada.
2. Receitas: o plano que deu certo num objetivo parecido aparece como exemplo no planejamento seguinte.
3. Lições de conversa: tiradas das suas reações (👍/👎 e comentário) às respostas dele; entram no contexto do chat.
   A reação numa resposta dá ou tira ponto das lições de conversa que estavam ativas quando ela foi escrita.

O modelo propõe a lição; o código decide (evidência real, sem dado sensível, sem duplicar, limites). Nada disso
muda os pesos do modelo: o que muda é o que ele recebe ao planejar e conversar, a partir do placar guardado.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, Sequence

from app.memory.text import is_duplicate, jaccard, looks_sensitive, token_set

MAX_LESSON_CHARS = 280
MAX_ACTIVE = 60          # por escopo; acima disso a pior é aposentada
RETIRE_AFTER_LOSSES = 3
SIMILAR = 0.6
RECIPE_OVERLAP = 0.25


def _score(row: Any, overlap: float = 0.0) -> float:
    wins, losses = row["wins"], row["losses"]
    return overlap * 2 + (wins + 1) / (wins + losses + 2) + 0.1 * min(row["reinforced"], 5)


def clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())[:MAX_LESSON_CHARS]
    if len(text) < 15 or looks_sensitive(text):
        return None
    return text


def add(conn: sqlite3.Connection, scope: str, text: str, tools: Sequence[str], evidence: dict[str, Any], now: str) -> dict[str, Any]:
    """Grava ou reforça. Devolve {"id", "new"}."""
    for r in conn.execute("SELECT * FROM lessons WHERE scope = ? AND status = 'active'", (scope,)).fetchall():
        if is_duplicate(text, r["text"], threshold=SIMILAR) or jaccard(token_set(text), token_set(r["text"])) >= SIMILAR:
            ev = json.loads(r["evidence"] or "{}")
            ev.setdefault("more", []).append(evidence)
            conn.execute("UPDATE lessons SET reinforced = reinforced + 1, evidence = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(ev, ensure_ascii=False)[:4000], now, r["id"]))
            return {"id": r["id"], "new": False}
    cur = conn.execute(
        "INSERT INTO lessons (scope, text, tools, evidence, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
        (scope, text, json.dumps(list(tools)), json.dumps(evidence, ensure_ascii=False), now, now))
    lid = int(cur.lastrowid)
    active = conn.execute("SELECT * FROM lessons WHERE scope = ? AND status = 'active'", (scope,)).fetchall()
    if len(active) > MAX_ACTIVE:
        worst = min((r for r in active if r["id"] != lid), key=_score)
        conn.execute("UPDATE lessons SET status = 'retired', retired_reason = 'muitas lições; esta era a menos útil', "
                     "updated_at = ? WHERE id = ?", (now, worst["id"]))
    return {"id": lid, "new": True}


def relevant(conn: sqlite3.Connection, scope: str, text: str, limit: int = 4) -> list[dict[str, Any]]:
    words = token_set(text)
    scored = []
    for r in conn.execute("SELECT * FROM lessons WHERE scope = ? AND status = 'active'", (scope,)).fetchall():
        overlap = jaccard(words, token_set(r["text"] + " " + " ".join(json.loads(r["tools"] or "[]")))) if words else 0.0
        if scope == "task" and overlap < 0.05 and r["wins"] < 2:
            continue                    # lição de tarefa só entra se tiver a ver, ou se já provou que ajuda
        scored.append((_score(r, overlap), dict(r)))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:limit]]


def block(rows: Sequence[dict[str, Any]], scope: str) -> str:
    if not rows:
        return ""
    head = ("O que você aprendeu fazendo coisas parecidas (lições suas, com o placar real; siga as que ajudam):"
            if scope == "task" else
            "Jeitos de conversar que você aprendeu com as reações dele (lições suas):")
    lines = [f"- {r['text']} (ajudou {r['wins']}x, falhou {r['losses']}x)" for r in rows]
    return head + "\n" + "\n".join(lines)


def mark_used(conn: sqlite3.Connection, lesson_ids: Iterable[int], target: str, target_id: int, now: str) -> None:
    for lid in lesson_ids:
        conn.execute("INSERT INTO lesson_uses (lesson_id, target, target_id, created_at) VALUES (?, ?, ?, ?)",
                     (lid, target, target_id, now))
        conn.execute("UPDATE lessons SET uses = uses + 1 WHERE id = ?", (lid,))


def settle(conn: sqlite3.Connection, target: str, target_id: int, success: bool, now: str) -> list[int]:
    """Resultado chegou: dá ou tira ponto das lições usadas ali (uma vez). Aposenta as que falham mais do que ajudam."""
    rows = conn.execute("SELECT id, lesson_id FROM lesson_uses WHERE target = ? AND target_id = ? AND outcome IS NULL",
                        (target, target_id)).fetchall()
    touched = []
    for u in rows:
        conn.execute("UPDATE lesson_uses SET outcome = ? WHERE id = ?", (1 if success else -1, u["id"]))
        col = "wins" if success else "losses"
        conn.execute(f"UPDATE lessons SET {col} = {col} + 1, updated_at = ? WHERE id = ?", (now, u["lesson_id"]))  # noqa: S608
        touched.append(u["lesson_id"])
        r = conn.execute("SELECT * FROM lessons WHERE id = ?", (u["lesson_id"],)).fetchone()
        if r and r["status"] == "active" and r["losses"] >= RETIRE_AFTER_LOSSES and r["wins"] * 2 < r["losses"]:
            conn.execute("UPDATE lessons SET status = 'retired', retired_reason = ?, updated_at = ? WHERE id = ?",
                         (f"falhou {r['losses']}x e ajudou {r['wins']}x", now, r["id"]))
    return touched


def change_outcome(conn: sqlite3.Connection, target: str, target_id: int, success: bool, now: str) -> None:
    """Para reações trocadas (👍 virou 👎): desfaz o ponto anterior e aplica o novo."""
    for u in conn.execute("SELECT id, lesson_id, outcome FROM lesson_uses WHERE target = ? AND target_id = ? AND outcome IS NOT NULL",
                          (target, target_id)).fetchall():
        new = 1 if success else -1
        if u["outcome"] == new:
            continue
        old_col, new_col = ("wins", "losses") if u["outcome"] == 1 else ("losses", "wins")
        conn.execute(f"UPDATE lessons SET {old_col} = MAX(0, {old_col} - 1), {new_col} = {new_col} + 1, updated_at = ? "  # noqa: S608
                     "WHERE id = ?", (now, u["lesson_id"]))
        conn.execute("UPDATE lesson_uses SET outcome = ? WHERE id = ?", (new, u["id"]))
    settle(conn, target, target_id, success, now)


def retire(conn: sqlite3.Connection, lesson_id: int, reason: str, now: str) -> bool:
    return conn.execute("UPDATE lessons SET status = 'retired', retired_reason = ?, updated_at = ? WHERE id = ? AND status = 'active'",
                        (reason[:200], now, lesson_id)).rowcount > 0


def restore(conn: sqlite3.Connection, lesson_id: int, now: str) -> bool:
    return conn.execute("UPDATE lessons SET status = 'active', retired_reason = NULL, losses = 0, updated_at = ? "
                        "WHERE id = ? AND status = 'retired'", (now, lesson_id)).rowcount > 0


def listing(conn: sqlite3.Connection, include_retired: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM lessons" + ("" if include_retired else " WHERE status = 'active'") + " ORDER BY status, id DESC"
    out = []
    for r in conn.execute(sql):
        d = dict(r)
        d["tools"] = json.loads(d["tools"] or "[]")
        d["evidence"] = json.loads(d["evidence"] or "{}")
        out.append(d)
    return out


# --- receitas: planos que deram certo ------------------------------------------------------------------------------
def similar_recipe(conn: sqlite3.Connection, description: str, exclude_goal: int | None = None) -> str:
    words = token_set(description)
    if not words:
        return ""
    best, best_overlap = None, RECIPE_OVERLAP
    for g in conn.execute("SELECT id, description FROM goals WHERE mode = 'task' AND status = 'done' ORDER BY id DESC LIMIT 200"):
        if g["id"] == exclude_goal:
            continue
        overlap = jaccard(words, token_set(g["description"]))
        if overlap > best_overlap:
            best, best_overlap = g, overlap
    if best is None:
        return ""
    steps = conn.execute("SELECT tool, title, args FROM tasks WHERE goal_id = ? AND status = 'done' ORDER BY step, id",
                         (best["id"],)).fetchall()
    if not steps:
        return ""
    lines = [f"{i}. {s['tool']}: {s['title']} {s['args'][:160]}" for i, s in enumerate(steps, 1)]
    return (f"Da última vez que você fez algo parecido (\"{best['description'][:160]}\"), deu certo assim "
            "(adapte, não copie):\n" + "\n".join(lines))
