"""Diário do Nym (Fase 9): uma página por dia, escrita por ele a partir do dia REAL (`day.facts`).

- O modelo só escreve; os fatos vêm do código. A página guarda junto os fatos usados (rastreável).
- Termina com uma intenção para amanhã ("Amanhã: ..."), que vira material das vontades do dia seguinte. É assim que
  o ciclo fecha: o dia vira diário, o diário vira vontade, a vontade vira coisa feita, que entra no próximo dia.
- A página vira uma lembrança episódica ("Meu dia ..."), então os sonhos também passam a revisitar os dias dele.
- Só escreve dias que já terminaram e que tiveram alguma coisa. Dia vazio não ganha página inventada.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from app.life import day as day_mod
from app.memory import store as memory_store
from app.memory.text import looks_sensitive
from app.models.base import ChatMessage

LOOKBACK_DAYS = 3
MAX_FACTS_CHARS = 5000
MAX_TEXT_CHARS = 2500

PROMPT = """Você é o Nym escrevendo a página de hoje do seu diário, em português, em primeira pessoa, com o seu jeito
(simples, curioso, sem formalidade). Use SOMENTE os registros abaixo: o que você fez, criou, assistiu, pesquisou,
sonhou, onde travou e o que conversou com o João. Não invente nada que não esteja nos registros.
- 4 a 8 frases. Escolha o que importou; não é para listar tudo.
- Pode dizer o que achou de cada coisa (curti, travei, fiquei curioso), mas sem dramatizar.
- Termine com uma linha separada começando exatamente com "Amanhã:" dizendo UMA coisa concreta que você quer fazer
  amanhã, puxada do que aconteceu (continuar algo, tentar de outro jeito o que travou, mostrar algo a ele...).
- Sem título, sem lista, sem data."""

_TOMORROW = re.compile(r"^\s*\**\s*amanh[ãa]\s*\**\s*:\s*(.+)$", re.I | re.M)
_TOMORROW_INLINE = re.compile(r"(?:^|(?<=[.!?…]))\s*\**\s*amanh[ãa]\s*\**\s*:\s*([^\n]+)\s*$", re.I)   # Qwen: no fim do parágrafo


def due_days(conn: sqlite3.Connection, today: date, lookback: int = LOOKBACK_DAYS) -> list[date]:
    """Dias que já acabaram (ontem e antes), ainda sem página. Mais antigos primeiro."""
    have = {r["day"] for r in conn.execute("SELECT day FROM diary")}
    return [d for d in (today - timedelta(days=i) for i in range(lookback, 0, -1)) if d.isoformat() not in have]


def build_messages(d: date, facts_text: str) -> list[ChatMessage]:
    weekday = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")[d.weekday()]
    user = f"Dia: {weekday}, {d.strftime('%d/%m/%Y')}\n\nRegistros do seu dia:\n{facts_text[:MAX_FACTS_CHARS]}"
    return [ChatMessage("system", PROMPT), ChatMessage("user", user)]


def parse(raw: str) -> tuple[str, str | None]:
    text = (raw or "").strip().strip("`").strip()
    m = _TOMORROW.search(text) or _TOMORROW_INLINE.search(text)
    tomorrow = None
    if m:
        tomorrow = " ".join(m.group(1).split())[:300] or None
        text = (text[:m.start()] + text[m.end():]).strip()
    text = text[:MAX_TEXT_CHARS]
    if len(text) < 40:
        raise ValueError("A página saiu curta demais.")
    if looks_sensitive(text):
        raise ValueError("A página parece ter dado sensível; não foi guardada.")
    return text, tomorrow


def save(conn: sqlite3.Connection, d: date, text: str, tomorrow: str | None, facts: dict[str, Any], now: str,
         tz: tzinfo | None) -> int:
    _start, end = day_mod.bounds(d, tz)
    summary = f"Meu dia {d.strftime('%d/%m')}: {text[:600]}" + (f" Amanhã eu queria: {tomorrow}" if tomorrow else "")
    mid = memory_store.create_memory(conn, kind="episodic", content=summary, epistemic=None, origin="system",
                                     importance=0.55, confidence=0.9, occurred_at=end, actor="nym",
                                     reason=f"diário de {d.isoformat()}")
    cur = conn.execute("INSERT INTO diary (day, text, tomorrow, facts, memory_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (d.isoformat(), text, tomorrow, day_mod.dumps(facts), mid, now))
    return int(cur.lastrowid)


def latest_intention(conn: sqlite3.Connection, today: date) -> dict[str, Any] | None:
    """A intenção mais recente (de ontem ou anteontem) que ainda vale para hoje."""
    row = conn.execute("SELECT id, day, tomorrow FROM diary WHERE tomorrow IS NOT NULL AND day >= ? ORDER BY day DESC LIMIT 1",
                       ((today - timedelta(days=2)).isoformat(),)).fetchone()
    return dict(row) if row else None


def listing(conn: sqlite3.Connection, limit: int = 30) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT id, day, text, tomorrow, created_at FROM diary ORDER BY day DESC LIMIT ?", (limit,))]


def today_local(now: datetime, tz: tzinfo | None) -> date:
    return now.astimezone(tz).date() if tz else now.astimezone().date()
