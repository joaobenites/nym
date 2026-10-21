"""Consolidação: transforma trechos do chat único em memórias, em segundo plano.

Fluxo: (1) PLANEJAR: o chat é cortado em trechos (episódios) por pausa longa ou tamanho; cada
trecho fechado vira um `memory_job`. (2) PROCESSAR: o modelo resume e extrai itens com
evidências; o código valida e grava TUDO em uma única transação (episódio, fatos, fontes,
ligações). Se o modelo está indisponível, o job fica pendente (nada se perde); respostas
inválidas contam tentativas e, esgotadas, o job vira "failed" (visível ao usuário).
Um único consolidador roda por vez (trava): nunca duas consolidações alterando as mesmas memórias.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from app.config import Settings
from app.core.activity import ActivityGate
from app.database import repository as repo
from app.database.connection import connect, transaction
from app.database.repository import log_audit, utcnow
from app.core import followups
from app.senses import vision
from app.memory import store
from app.memory.extraction import Extraction, ExtractionError, Item, build_messages, parse_extraction
from app.memory.text import jaccard, token_set
from app.models.base import LLMProvider, ModelError, ModelUnavailableError

log = logging.getLogger("nym.memory")

MAIN_CONVERSATION_KEY = "main_conversation_id"
PLANNED_KEY = "memory_planned_until_message_id"
SEGMENT_MAX_MESSAGES = 30
SEGMENT_MAX_CHARS = 8000
MIN_USER_CHARS = 40          # trechos com quase nada do usuário ("oi", "tchau") não viram memória
MAX_RELATED_EXISTING = 15   # contradições quase nunca repetem as palavras da fala nova
SUPERSEDE_MIN_OVERLAP = 0.2  # só substitui memória antiga se houver assunto em comum


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def split_segments(rows: Sequence[sqlite3.Row], gap: timedelta) -> list[list[sqlite3.Row]]:
    segments: list[list[sqlite3.Row]] = []
    chars = 0
    for row in rows:
        new = not segments
        if segments:
            prev = segments[-1][-1]
            new = (_parse_time(row["created_at"]) - _parse_time(prev["created_at"]) > gap
                   or len(segments[-1]) >= SEGMENT_MAX_MESSAGES
                   or chars + len(row["content"]) > SEGMENT_MAX_CHARS)
        if new:
            segments.append([])
            chars = 0
        segments[-1].append(row)
        chars += len(row["content"])
    return segments


class Consolidator:
    def __init__(self, settings: Settings, provider: LLMProvider, clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()

    # --- API pública ---------------------------------------------------------------
    def run_once(self, *, force: bool = False, max_jobs: int | None = None) -> dict[str, Any]:
        """Planeja trechos novos e processa jobs pendentes. `force` fecha também o trecho em andamento."""
        if not self._lock.acquire(blocking=False):
            return {"status": "busy", "planned": 0, "processed": [], "pending": None}
        try:
            conn = connect(self._settings.db_path)
            try:
                planned = self._plan(conn, force)
                processed = []
                pending = conn.execute("SELECT * FROM memory_jobs WHERE status = 'pending' ORDER BY id").fetchall()
                for job in pending:
                    if max_jobs is not None and len(processed) >= max_jobs:
                        break
                    result = self._process(conn, job)
                    processed.append(result)
                    if result["status"] == "unavailable":
                        break  # sem modelo, não adianta tentar os outros agora
                left = conn.execute("SELECT COUNT(*) FROM memory_jobs WHERE status = 'pending'").fetchone()[0]
                return {"status": "ok", "planned": planned, "processed": processed, "pending": left}
            finally:
                conn.close()
        finally:
            self._lock.release()

    # --- planejamento ------------------------------------------------------------------
    def _plan(self, conn: sqlite3.Connection, force: bool) -> int:
        main = repo.get_state(conn, MAIN_CONVERSATION_KEY)
        if not main:
            return 0
        planned_until = int(repo.get_state(conn, PLANNED_KEY) or 0)
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? AND id > ? ORDER BY id",
            (main, planned_until),
        ).fetchall()
        if not rows:
            return 0
        gap = timedelta(minutes=self._settings.episode_gap_minutes)
        segments = split_segments(rows, gap)
        if not force and self._clock() - _parse_time(rows[-1]["created_at"]) <= gap:
            segments = segments[:-1]  # o último trecho ainda está acontecendo
        if not segments:
            return 0
        created = 0
        with transaction(conn):
            for seg in segments:
                user_chars = sum(len(r["content"]) for r in seg if r["role"] == "user")
                status = "pending" if user_chars >= MIN_USER_CHARS else "skipped"
                now = utcnow()
                cur = conn.execute(
                    "INSERT OR IGNORE INTO memory_jobs (start_message_id, end_message_id, message_count, status, "
                    "attempts, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
                    (seg[0]["id"], seg[-1]["id"], len(seg), status, now, now),
                )
                created += cur.rowcount
            repo.set_state(conn, PLANNED_KEY, str(segments[-1][-1]["id"]))
        return created

    # --- processamento ---------------------------------------------------------------------
    def _process(self, conn: sqlite3.Connection, job: sqlite3.Row) -> dict[str, Any]:
        main = repo.get_state(conn, MAIN_CONVERSATION_KEY)
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? "
            "AND id BETWEEN ? AND ? ORDER BY id", (main, job["start_message_id"], job["end_message_id"]),
        ).fetchall()
        if not rows:
            self._finish(conn, job["id"], "skipped", "mensagens removidas", None)
            return {"job_id": job["id"], "status": "skipped"}

        message_ids = {r["id"] for r in rows}
        user_ids = {r["id"] for r in rows if r["role"] == "user"}
        existing = self._related_existing(conn, rows)
        try:
            response = self._provider.chat(build_messages(self._with_perceptions(conn, rows), existing),
                                           temperature=0.2, json_mode=True)
        except ModelUnavailableError as exc:
            self._note_error(conn, job["id"], str(exc))
            return {"job_id": job["id"], "status": "unavailable", "error": str(exc)}
        except ModelError as exc:
            return self._fail(conn, job, str(exc))
        try:
            data = parse_extraction(response.content, message_ids=message_ids, user_ids=user_ids,
                                    existing_ids={m["id"] for m in existing})
        except ExtractionError as exc:
            return self._fail(conn, job, str(exc))

        try:
            with transaction(conn):  # tudo ou nada: episódio, fatos, fontes e ligações
                episode_id, summary = self._apply(conn, job, rows, data)
        except Exception as exc:  # noqa: BLE001 - a transação já foi desfeita
            return self._fail(conn, job, f"erro ao gravar a memória: {exc}")
        log.info("Job %s consolidado: episódio #%s (%s fatos, %s observações).",
                 job["id"], episode_id, summary["facts"], summary["identity_notes"])
        return {"job_id": job["id"], "status": "done", "memory_id": episode_id, **summary}

    @staticmethod
    def _with_perceptions(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
        """Fase 8: o que ele mostrou (imagem, câmera, tela) entra no trecho, marcado como visto pelo Nym, não dito."""
        out = [dict(r) for r in rows]
        try:
            seen = vision.for_messages(conn, [r["id"] for r in out if r["role"] == "user"])
        except sqlite3.Error:
            return out
        for r in out:
            for p in seen.get(r["id"], []):
                if not p["description"].startswith("Não consegui enxergar"):
                    r["content"] += (f"\n[{vision.LABEL.get(p['kind'], p['kind'])} — descrito pela visão do Nym, "
                                     f"não dito pelo usuário: {p['description']}]")
        return out

    def _related_existing(self, conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
        """Memórias já registradas oferecidas ao modelo para detectar contradições e repetições.

        Ordem: mais palavras em comum primeiro, depois as mais importantes. Não filtra por sobreposição:
        "mudei para Braga" contradiz "mora no Porto" sem compartilhar nenhuma palavra."""
        user_tokens = token_set(" ".join(r["content"] for r in rows if r["role"] == "user"))
        scored = []
        for m in conn.execute("SELECT id, content, importance FROM memories WHERE status = 'active' AND kind = 'semantic'"):
            overlap = len(user_tokens & token_set(m["content"]))
            scored.append((-overlap, -m["importance"], m["id"], {"id": m["id"], "content": m["content"]}))
        scored.sort(key=lambda t: t[:3])
        return [m for *_, m in scored[:MAX_RELATED_EXISTING]]

    def _apply(self, conn: sqlite3.Connection, job: sqlite3.Row, rows: Sequence[sqlite3.Row], data: Extraction) -> tuple[int, dict[str, int]]:
        previous = conn.execute(
            "SELECT id FROM memories WHERE kind = 'episodic' ORDER BY occurred_at DESC, id DESC LIMIT 1").fetchone()
        episode_id = store.create_memory(
            conn, kind="episodic", content=data.summary, outcome=data.outcome, epistemic=None, origin="system",
            importance=data.importance, confidence=0.7, entities=data.participants,
            occurred_at=rows[0]["created_at"], ended_at=rows[-1]["created_at"],
            message_ids=[r["id"] for r in rows], reason="consolidação automática",
        )
        if previous:
            store.add_link(conn, episode_id, previous["id"], "follows")
        counts = {"facts": 0, "identity_notes": 0, "merged": 0}
        for item in data.facts:
            if self._store_item(conn, "semantic", item, episode_id):
                counts["merged"] += 1
            counts["facts"] += 1
        for item in data.identity_notes:
            if self._store_item(conn, "identity", item, episode_id):
                counts["merged"] += 1
            counts["identity_notes"] += 1
        counts["followups"] = 0
        said_at = _parse_time(rows[-1]["created_at"])
        for f in data.followups:
            if followups.add(conn, f.text, f.about, followups.due_time(f.due, said_at), f.evidence, episode_id, utcnow()):
                counts["followups"] += 1
        self._finish(conn, job["id"], "done", None, episode_id)
        log_audit(conn, "memory.consolidated", {"job_id": job["id"], "episode_id": episode_id, **counts})
        return episode_id, counts

    def _store_item(self, conn: sqlite3.Connection, kind: str, item: Item, episode_id: int) -> bool:
        """Grava um item. Retorna True se foi fundido numa memória existente (recorrência)."""
        dup = store.find_similar(conn, item.text, kind)
        if dup is not None:
            store.bump_recurrence(conn, dup["id"], confidence=item.confidence, message_ids=item.evidence,
                                  reason=f"episódio #{episode_id}")
            store.add_link(conn, dup["id"], episode_id, "part_of")
            if kind == "semantic" and item.stated and dup["epistemic"] == "hypothesis":
                store.update_memory(conn, dup["id"], epistemic="confirmed", actor="system",
                                    reason=f"o usuário afirmou diretamente (episódio #{episode_id})")
                conn.execute("UPDATE memories SET origin = 'user_stated' WHERE id = ?", (dup["id"],))
            return True
        stated = item.stated and kind == "semantic"
        new_id = store.create_memory(
            conn, kind=kind, content=item.text,
            epistemic="confirmed" if stated else "hypothesis",
            origin="user_stated" if stated else "nym_inferred",
            importance=item.importance, confidence=item.confidence,
            message_ids=item.evidence, reason=f"episódio #{episode_id}",
        )
        store.add_link(conn, new_id, episode_id, "part_of")
        if kind == "semantic":
            self._resolve_contradictions(conn, new_id, item, stated)
        return False

    @staticmethod
    def _resolve_contradictions(conn: sqlite3.Connection, new_id: int, item: Item, stated: bool) -> None:
        for old_id in item.contradicts:
            old = store.get_memory(conn, old_id)
            if old is None or old["status"] != "active" or old["kind"] != "semantic":
                continue
            store.add_link(conn, new_id, old_id, "contradicts")
            overlap = jaccard(token_set(item.text), token_set(old["content"]))
            if stated and old["origin"] != "external_source" and overlap >= SUPERSEDE_MIN_OVERLAP:
                store.supersede(conn, old_id, new_id, "system", f"substituída por #{new_id}: o usuário informou o contrário")
            else:  # incerteza registrada, nada é apagado
                store.add_history(conn, new_id, "contradiction_noted", None, None, "system", f"contradiz #{old_id}")
                store.add_history(conn, old_id, "contradiction_noted", None, None, "system", f"contradita por #{new_id}")

    # --- estado dos jobs ------------------------------------------------------------------------
    @staticmethod
    def _finish(conn: sqlite3.Connection, job_id: int, status: str, error: str | None, memory_id: int | None) -> None:
        conn.execute("UPDATE memory_jobs SET status = ?, last_error = ?, memory_id = ?, updated_at = ? WHERE id = ?",
                     (status, error, memory_id, utcnow(), job_id))

    @staticmethod
    def _note_error(conn: sqlite3.Connection, job_id: int, error: str) -> None:
        conn.execute("UPDATE memory_jobs SET last_error = ?, updated_at = ? WHERE id = ?", (error[:500], utcnow(), job_id))

    def _fail(self, conn: sqlite3.Connection, job: sqlite3.Row, error: str) -> dict[str, Any]:
        attempts = job["attempts"] + 1
        status = "failed" if attempts >= self._settings.memory_max_attempts else "pending"
        conn.execute("UPDATE memory_jobs SET attempts = ?, status = ?, last_error = ?, updated_at = ? WHERE id = ?",
                     (attempts, status, error[:500], utcnow(), job["id"]))
        log.warning("Job %s falhou (tentativa %s/%s): %s", job["id"], attempts, self._settings.memory_max_attempts, error)
        return {"job_id": job["id"], "status": status, "error": error, "attempts": attempts}


class MemoryWorker:
    """Thread que consolida em segundo plano, só quando o chat está parado."""

    def __init__(self, consolidator: Consolidator, gate: ActivityGate, interval: float, idle_seconds: float) -> None:
        self._consolidator = consolidator
        self._gate = gate
        self._interval = interval
        self._idle = idle_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> dict[str, Any] | None:
        if self._gate.is_busy() or self._gate.idle_seconds() < self._idle:
            return None
        return self._consolidator.run_once(max_jobs=1)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - o trabalhador nunca deve morrer por um erro isolado
                log.exception("Erro na consolidação em segundo plano")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nym-memory-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
