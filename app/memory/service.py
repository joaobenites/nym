"""Fachada da memória usada pela API e pelo chat. Cada chamada abre sua própria conexão."""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Collection

from app.config import Settings
from app.database.connection import connect, transaction
from app.database.repository import log_audit
from app.memory import store
from app.memory.retrieval import RetrievalWeights, format_block, hit_to_dict, retrieve
from app.memory.store import InvalidMemoryInput

log = logging.getLogger("nym.memory")


def _row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["entities"] = json.loads(data["entities"])
    return data


class MemoryService:
    def __init__(self, settings: Settings, weights: RetrievalWeights | None = None) -> None:
        self._settings = settings
        self._weights = weights or RetrievalWeights()

    def _conn(self) -> sqlite3.Connection:
        return connect(self._settings.db_path)

    # --- consulta -------------------------------------------------------------------
    def list(self, *, kind: str | None = None, epistemic: str | None = None, status: str | None = "active",
             q: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            return [_row(r) for r in store.list_memories(
                conn, kind=kind, epistemic=epistemic, status=status, q=q, limit=limit, offset=offset)]
        finally:
            conn.close()

    def get(self, memory_id: int) -> dict[str, Any]:
        conn = self._conn()
        try:
            row = store.require_memory(conn, memory_id)
            return {**_row(row), "sources": store.sources_of(conn, memory_id),
                    "links": store.links_of(conn, memory_id), "history": store.history_of(conn, memory_id)}
        finally:
            conn.close()

    def jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM memory_jobs ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),))]
        finally:
            conn.close()

    def identity_candidates(self) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            return store.recurring_identity_candidates(conn)
        finally:
            conn.close()

    # --- ferramentas do usuário (consultar e corrigir) -----------------------------------
    def create(self, content: str, kind: str = "semantic", epistemic: str = "confirmed", importance: float = 0.7) -> dict[str, Any]:
        if kind not in ("semantic", "identity"):
            raise InvalidMemoryInput("Só é possível criar memórias do tipo 'semantic' ou 'identity'.")
        conn = self._conn()
        try:
            with transaction(conn):
                dup = store.find_similar(conn, content, kind) if isinstance(content, str) else None
                if dup is not None:
                    raise InvalidMemoryInput(f"Já existe uma lembrança parecida (#{dup['id']}).")
                memory_id = store.create_memory(
                    conn, kind=kind, content=content, epistemic=epistemic, origin="user_edit",
                    importance=importance, confidence=0.9, manual=True, actor="user", reason="criada pelo usuário")
                log_audit(conn, "memory.created", {"memory_id": memory_id, "kind": kind, "by": "user"})
            return _row(store.require_memory(conn, memory_id))
        finally:
            conn.close()

    def update(self, memory_id: int, *, content: str | None = None, epistemic: str | None = None,
               importance: float | None = None) -> dict[str, Any]:
        conn = self._conn()
        try:
            with transaction(conn):
                row = store.update_memory(conn, memory_id, content=content, epistemic=epistemic,
                                          importance=importance, actor="user", reason="edição do usuário")
                log_audit(conn, "memory.updated", {"memory_id": memory_id, "by": "user"})
            return _row(row)
        finally:
            conn.close()

    def archive(self, memory_id: int) -> dict[str, Any]:
        return self._set_status(memory_id, "archived")

    def restore(self, memory_id: int) -> dict[str, Any]:
        return self._set_status(memory_id, "active")

    def _set_status(self, memory_id: int, status: str) -> dict[str, Any]:
        conn = self._conn()
        try:
            with transaction(conn):
                store.set_status(conn, memory_id, status, "user", "ação do usuário")
            return _row(store.require_memory(conn, memory_id))
        finally:
            conn.close()

    def delete(self, memory_id: int, purge_sources: bool = False) -> dict[str, Any]:
        conn = self._conn()
        try:
            with transaction(conn):
                result = store.delete_memory(conn, memory_id, purge_sources=purge_sources)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # tira o conteúdo apagado do arquivo -wal
            except sqlite3.Error:
                log.warning("Não foi possível truncar o WAL após a exclusão.")
            return result
        finally:
            conn.close()

    def dedupe(self, dry_run: bool = True) -> list[dict[str, int]]:
        conn = self._conn()
        try:
            if dry_run:
                return store.dedupe_batch(conn, dry_run=True)
            with transaction(conn):
                merges = store.dedupe_batch(conn, dry_run=False)
                log_audit(conn, "memory.deduped", {"merges": merges})
            return merges
        finally:
            conn.close()

    # --- recuperação -------------------------------------------------------------------------
    def retrieve(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            return [hit_to_dict(h) for h in retrieve(conn, query, weights=self._weights, limit=limit)]
        finally:
            conn.close()

    def block_for_chat(self, query: str, exclude_message_ids: Collection[int]) -> tuple[str, list[int]]:
        conn = self._conn()
        try:
            hits = retrieve(conn, query, weights=self._weights, exclude_message_ids=exclude_message_ids)
            block, used = format_block(hits, self._settings.memory_chars)
            if used:
                with transaction(conn):
                    store.touch_accessed(conn, used)
            return block, used
        finally:
            conn.close()
