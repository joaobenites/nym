"""Diário de sonhos: leitura dos ciclos (para o usuário e para o chat) e controle de pausa.

Quando o Nym é perguntado sobre um sonho, o chat recebe o registro REAL daqui, e não uma lembrança inventada.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, tzinfo
from typing import Any

from app.config import Settings
from app.database.connection import connect, transaction
from app.database import repository as repo
from app.dreams import exploration
from app.core.timesense import parse_time, relative_moment
from app.memory.text import normalize

PAUSED_KEY = "dreams_paused"
DREAM_WORDS = ("sonh",)          # sonho, sonhou, sonhos, sonhando...


def asks_about_dreams(text: str) -> bool:
    return any(w in normalize(text) for w in DREAM_WORDS)


def _cycle_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["selected_ids"] = json.loads(d["selected_ids"])
    d["stage_log"] = json.loads(d["stage_log"])
    return d


def _item_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["refs"] = json.loads(d["refs"])
    return d


class DreamJournal:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _conn(self) -> sqlite3.Connection:
        return connect(self._settings.db_path)

    # --- diário ---------------------------------------------------------------------
    def list_cycles(self, limit: int = 30) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            return [_cycle_dict(r) for r in conn.execute(
                "SELECT * FROM dream_cycles ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),))]
        finally:
            conn.close()

    def get_cycle(self, cycle_id: int) -> dict[str, Any] | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM dream_cycles WHERE id = ?", (cycle_id,)).fetchone()
            if row is None:
                return None
            items = [_item_dict(r) for r in conn.execute("SELECT * FROM dream_items WHERE cycle_id = ? ORDER BY id", (cycle_id,))]
            return {**_cycle_dict(row), "items": items}
        finally:
            conn.close()

    def open_questions(self) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            return [_item_dict(r) for r in conn.execute(
                "SELECT * FROM dream_items WHERE kind = 'open_question' AND status = 'open' ORDER BY id DESC")]
        finally:
            conn.close()

    def close_question(self, item_id: int) -> bool:
        conn = self._conn()
        try:
            with transaction(conn):
                cur = conn.execute("UPDATE dream_items SET status = 'closed' WHERE id = ? AND kind = 'open_question'", (item_id,))
            return cur.rowcount > 0
        finally:
            conn.close()

    def explorations(self, status: str | None = None) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            return exploration.list_tasks(conn, status)
        finally:
            conn.close()

    def set_exploration(self, task_id: int, status: str) -> bool:
        conn = self._conn()
        try:
            with transaction(conn):
                return exploration.set_status(conn, task_id, status)
        finally:
            conn.close()

    # --- pausa e recuperação -------------------------------------------------------------
    def is_paused(self) -> bool:
        conn = self._conn()
        try:
            return repo.get_state(conn, PAUSED_KEY) == "1"
        finally:
            conn.close()

    def set_paused(self, paused: bool) -> None:
        conn = self._conn()
        try:
            with transaction(conn):
                repo.set_state(conn, PAUSED_KEY, "1" if paused else "0")
        finally:
            conn.close()

    def recover_stale(self) -> int:
        """Ciclos que ficaram 'running' (o programa foi encerrado no meio) viram 'interrupted'."""
        conn = self._conn()
        try:
            with transaction(conn):
                cur = conn.execute(
                    "UPDATE dream_cycles SET status = 'interrupted', ended_at = ?, error = 'programa encerrado durante o ciclo' "
                    "WHERE status = 'running'", (repo.utcnow(),))
            return cur.rowcount
        finally:
            conn.close()

    # --- chat ---------------------------------------------------------------------------
    def block_for_chat(self, query: str, since: str | None = None, now: datetime | None = None,
                       tz: tzinfo | None = None, limit: int = 2, max_chars: int = 1800) -> str:
        """O que o Nym sabe dos próprios sonhos nesta resposta, sempre a partir do registro REAL.

        - Perguntado sobre sonhos: os últimos ciclos, com ligações, hipóteses e perguntas.
        - Não perguntado, mas sonhou desde a última conversa (`since`): só um lembrete curto, para ele saber
          que sonhou enquanto o usuário estava fora (e poder comentar se vier a calhar).
        - Fora isso: nada."""
        conn = self._conn()
        try:
            if asks_about_dreams(query):
                return self._full_block(conn, limit, max_chars, now, tz)
            if since is None:
                return ""
            row = conn.execute(
                "SELECT * FROM dream_cycles WHERE status IN ('completed', 'partial') AND COALESCE(ended_at, started_at) > ? "
                "ORDER BY id DESC LIMIT 1", (since,)).fetchone()
            if row is None:
                return ""
            when = self._when(row, now, tz)
            text = (f"Sonho recente: enquanto vocês estavam sem conversar, você sonhou ({when}). "
                    f"O que ficou dele: {(row['review'] or 'nada que valesse registrar').strip()[:400]} "
                    "Se fizer sentido na conversa, pode comentar, do seu jeito; não precisa.")
            return text[:max_chars]
        finally:
            conn.close()

    @staticmethod
    def _when(row: sqlite3.Row, now: datetime | None, tz: tzinfo | None) -> str:
        then = parse_time(row["ended_at"] or row["started_at"])
        now = now or datetime.now(timezone.utc)
        return relative_moment(then.astimezone(tz), now.astimezone(tz))

    def _full_block(self, conn: sqlite3.Connection, limit: int, max_chars: int, now: datetime | None,
                    tz: tzinfo | None) -> str:
        cycles = conn.execute(
            "SELECT * FROM dream_cycles WHERE status IN ('completed', 'partial') ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        if not cycles:
            return ("Seus sonhos (registros reais): ainda não houve nenhum sonho registrado. "
                    "Se perguntarem, diga que não lembra de ter sonhado; não invente.")
        parts = ["Seus sonhos (registros reais; é isto que você sonhou, nada além. Conte como lembrança sua):"]
        for c in cycles:
            parts.append(f"- Sonho de {self._when(c, now, tz)}: {c['review'] or 'sem resumo'}")
            for it in conn.execute("SELECT kind, content FROM dream_items WHERE cycle_id = ? AND kind IN "
                                   "('association', 'hypothesis', 'open_question') ORDER BY id LIMIT 6", (c["id"],)):
                kind = {"association": "ligação", "hypothesis": "hipótese", "open_question": "pergunta em aberto"}[it["kind"]]
                parts.append(f"    · {kind}: {it['content']}")
        return "\n".join(parts)[:max_chars]
