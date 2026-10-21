"""Lembretes de perguntar depois (Fase 6.2): "tenho prova sexta" -> no dia, ele pergunta como foi.

Origem: a consolidação da memória (o modelo propõe; só vale com evidência numa mensagem do usuário).
Entrega: pela presença (ele puxa o assunto) ou, se vocês já estiverem conversando, no próprio chat; quando a resposta
dele toca no assunto, o lembrete é dado como entregue. Lembretes antigos demais expiram sem incomodar.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, Sequence

from app.memory.text import is_duplicate, jaccard, token_set

EXPIRE_AFTER = timedelta(days=4)
DEFAULT_DELAY = timedelta(hours=20)
MATCH_OVERLAP = 0.3


def due_time(due: str | None, said_at: datetime, tz: tzinfo | None = None) -> datetime:
    """Meio-dia (hora local) do dia indicado; sem dia, umas 20 h depois de ele ter contado. Nunca no passado."""
    if due:
        try:
            when = datetime.combine(date.fromisoformat(due), time(12, 0)).replace(tzinfo=tz or said_at.astimezone().tzinfo)
            return max(when.astimezone(timezone.utc), said_at.astimezone(timezone.utc) + timedelta(hours=2))
        except ValueError:
            pass
    return said_at.astimezone(timezone.utc) + DEFAULT_DELAY


def add(conn: sqlite3.Connection, text: str, about: str | None, due_at: datetime, evidence: Sequence[int],
        episode_id: int | None, now: str) -> int | None:
    for r in conn.execute("SELECT text, about FROM followups WHERE status = 'pending'").fetchall():
        if is_duplicate(text, r["text"]) or jaccard(token_set(text + " " + (about or "")),
                                                    token_set(r["text"] + " " + (r["about"] or ""))) >= 0.5:
            return None
    cur = conn.execute(
        "INSERT INTO followups (text, about, due_at, status, evidence, episode_id, created_at) VALUES (?, ?, ?, 'pending', ?, ?, ?)",
        (text, about, due_at.isoformat(timespec="milliseconds"), json.dumps(list(evidence)), episode_id, now))
    return int(cur.lastrowid)


def due(conn: sqlite3.Connection, now: datetime, limit: int = 2) -> list[dict[str, Any]]:
    """Lembretes que já podem ser perguntados. Os muito antigos expiram aqui."""
    stamp = now.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    old = (now - EXPIRE_AFTER).astimezone(timezone.utc).isoformat(timespec="milliseconds")
    conn.execute("UPDATE followups SET status = 'expired' WHERE status = 'pending' AND due_at < ?", (old,))
    return [dict(r) for r in conn.execute(
        "SELECT * FROM followups WHERE status = 'pending' AND due_at <= ? ORDER BY due_at LIMIT ?", (stamp, limit))]


def mark_delivered(conn: sqlite3.Connection, followup_id: int, now: str) -> None:
    conn.execute("UPDATE followups SET status = 'delivered', delivered_at = ? WHERE id = ? AND status = 'pending'",
                 (now, followup_id))


def block_for_chat(items: Sequence[dict[str, Any]]) -> str:
    if not items:
        return ""
    lines = "\n".join(f"- {f['text']}" + (f" ({f['about']})" if f.get("about") else "") for f in items)
    return ("Coisas que você queria perguntar a ele (ele mesmo contou antes; se encaixar agora, pergunte do seu jeito, "
            "sem dizer que anotou):\n" + lines)


def asked_in(reply: str, item: dict[str, Any]) -> bool:
    words = token_set(reply)
    target = token_set(item["text"] + " " + (item.get("about") or ""))
    return bool(target) and (jaccard(words & target, target) >= MATCH_OVERLAP or len(words & target) >= 2)
