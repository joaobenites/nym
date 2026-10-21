"""O dia do Nym (Fase 9 — vida contínua): o que aconteceu de verdade num intervalo de tempo, montado só dos registros.

Nada aqui chama o modelo. É a base do diário, do "enquanto você estava fora" e da página Vida. Se não está nos
registros (objetivos, criações, vídeos, pesquisas, sonhos, conversa, lições, reações), não está no dia.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any

MAX_ITEMS = 8


def bounds(day: date, tz: tzinfo | None) -> tuple[str, str]:
    """Início e fim do dia LOCAL, em UTC (o banco guarda UTC)."""
    tz = tz or datetime.now().astimezone().tzinfo
    start = datetime.combine(day, time(0, 0)).replace(tzinfo=tz)
    end = start + timedelta(days=1)
    iso = lambda d: d.astimezone(timezone.utc).isoformat(timespec="milliseconds")  # noqa: E731
    return iso(start), iso(end)


def facts(conn: sqlite3.Connection, start: str, end: str) -> dict[str, Any]:
    q = lambda sql, *a: [dict(r) for r in conn.execute(sql, a)]  # noqa: E731
    out: dict[str, Any] = {}
    out["done"] = q("SELECT g.id, g.description, substr(COALESCE(g.result, ''), 1, 240) AS result, g.origin, p.title AS project "
                    "FROM goals g LEFT JOIN projects p ON p.id = g.project_id WHERE g.mode = 'task' AND g.status = 'done' "
                    "AND g.updated_at >= ? AND g.updated_at < ? ORDER BY g.id LIMIT ?", start, end, MAX_ITEMS)
    out["started"] = q("SELECT id, description, substr(COALESCE(motivation, ''), 1, 200) AS why FROM goals "
                       "WHERE mode = 'task' AND origin = 'nym' AND created_at >= ? AND created_at < ? ORDER BY id LIMIT ?",
                       start, end, MAX_ITEMS)
    out["stuck"] = q("SELECT DISTINCT g.id, g.description, substr(COALESCE(g.last_error, ''), 1, 160) AS why FROM task_events e "
                     "JOIN goals g ON g.id = e.goal_id WHERE e.event IN ('goal.paused', 'goal.retry_scheduled') "
                     "AND e.created_at >= ? AND e.created_at < ? LIMIT ?", start, end, MAX_ITEMS)
    out["creations"] = q("SELECT id, kind, title, version FROM creations WHERE created_at >= ? AND created_at < ? ORDER BY id LIMIT ?",
                         start, end, MAX_ITEMS)
    out["watched"] = q("SELECT id, title, source FROM perceptions WHERE kind = 'video' AND created_at >= ? AND created_at < ? "
                       "ORDER BY id LIMIT ?", start, end, MAX_ITEMS)
    out["seen"] = q("SELECT id, kind, substr(description, 1, 160) AS description FROM perceptions WHERE kind != 'video' "
                    "AND description NOT LIKE 'Não consegui enxergar%' AND created_at >= ? AND created_at < ? ORDER BY id LIMIT ?",
                    start, end, 4)
    searches = q("SELECT DISTINCT query FROM research_log WHERE kind = 'search' AND status IN ('ok', 'cached') "
                 "AND created_at >= ? AND created_at < ? LIMIT 6", start, end)
    out["searches"] = [s["query"] for s in searches]
    out["dreams"] = q("SELECT id, substr(COALESCE(review, ''), 1, 240) AS review FROM dream_cycles WHERE status IN ('completed', 'partial') "
                      "AND COALESCE(ended_at, started_at) >= ? AND COALESCE(ended_at, started_at) < ? ORDER BY id LIMIT 3", start, end)
    main = conn.execute("SELECT value FROM app_state WHERE key = 'main_conversation_id'").fetchone()
    talk = {"user": 0, "nym": 0, "initiative": 0}
    if main:
        for r in conn.execute("SELECT role, initiative, COUNT(*) AS n FROM messages WHERE conversation_id = ? AND created_at >= ? "
                              "AND created_at < ? GROUP BY role, initiative", (main["value"], start, end)):
            if r["role"] == "user":
                talk["user"] += r["n"]
            else:
                talk["nym"] += r["n"]
                talk["initiative"] += r["n"] if r["initiative"] else 0
    out["talk"] = talk
    out["topics"] = [r["content"] for r in conn.execute(
        "SELECT substr(m.content, 1, 200) AS content FROM memories m WHERE m.kind = 'episodic' AND m.status = 'active' "
        "AND m.created_at >= ? AND m.created_at < ? AND EXISTS (SELECT 1 FROM memory_sources s WHERE s.memory_id = m.id "
        "AND s.source_type = 'message') ORDER BY m.id LIMIT 4", (start, end))]
    out["lessons"] = [r["text"] for r in conn.execute(
        "SELECT text FROM lessons WHERE created_at >= ? AND created_at < ? ORDER BY id LIMIT 3", (start, end))]
    likes = conn.execute("SELECT SUM(value = 1), SUM(value = -1) FROM feedback WHERE created_at >= ? AND created_at < ?",
                         (start, end)).fetchone()
    out["reactions"] = {"likes": likes[0] or 0, "dislikes": likes[1] or 0}
    return out


def is_empty(f: dict[str, Any]) -> bool:
    keys = ("done", "started", "stuck", "creations", "watched", "seen", "searches", "dreams", "topics", "lessons")
    return not any(f.get(k) for k in keys) and not f["talk"]["user"]


KIND = {"text": "texto", "drawing": "desenho", "toy": "brinquedo", "code": "código"}


def render(f: dict[str, Any]) -> str:
    """Lista objetiva (sem modelo) do que aconteceu. Usada nos prompts e na página Vida."""
    lines: list[str] = []
    t = f["talk"]
    if t["user"]:
        lines.append(f"Conversou com ele: {t['user']} mensagem(ns) dele" + (f"; você puxou assunto {t['initiative']}x" if t["initiative"] else ""))
    for x in f["topics"]:
        lines.append(f"Assunto da conversa: {x}")
    for g in f["done"]:
        lines.append(f"Terminou: {g['description']}" + (f" (projeto {g['project']})" if g.get("project") else "")
                     + (f" — {g['result']}" if g["result"] else ""))
    for g in f["started"]:
        lines.append(f"Decidiu fazer: {g['description']}" + (f" (porque: {g['why']})" if g["why"] else ""))
    for g in f["stuck"]:
        lines.append(f"Travou em: {g['description']}" + (f" ({g['why']})" if g["why"] else ""))
    for c in f["creations"]:
        lines.append(f"Criou: {KIND.get(c['kind'], c['kind'])} \"{c['title']}\"" + (f" (versão {c['version']})" if c["version"] > 1 else ""))
    for v in f["watched"]:
        lines.append(f"Assistiu: \"{v['title'] or v['source']}\"")
    for s in f["seen"]:
        lines.append(f"Viu ({s['kind']}): {s['description']}")
    if f["searches"]:
        lines.append("Pesquisou: " + "; ".join(f["searches"]))
    for d in f["dreams"]:
        lines.append(f"Sonhou: {d['review']}")
    for le in f["lessons"]:
        lines.append(f"Aprendeu: {le}")
    r = f["reactions"]
    if r["likes"] or r["dislikes"]:
        lines.append(f"Reações dele: {r['likes']} curtida(s), {r['dislikes']} não curtida(s)")
    return "\n".join(f"- {line}" for line in lines)


def since(conn: sqlite3.Connection, start: str, end: str) -> str:
    """'Enquanto você estava fora': o que ELE fez sozinho no intervalo (sem a conversa em si)."""
    f = facts(conn, start, end)
    f["talk"] = {"user": 0, "nym": 0, "initiative": 0}
    f["topics"] = []
    return "" if is_empty(f) else render(f)


def dumps(f: dict[str, Any]) -> str:
    return json.dumps(f, ensure_ascii=False)
