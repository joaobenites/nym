"""Ateliê e aprendizado vistos pelo usuário (Fase 7): o que a API chama. Ações aqui são SEMPRE do usuário."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.config import Settings
from app.creations import selfcode, store
from app.database.connection import connect, transaction
from app.database.repository import log_audit
from app.learning import feedback, lessons


class AtelierError(ValueError):
    pass


class AtelierNotFound(LookupError):
    pass


class AtelierService:
    def __init__(self, settings: Settings, clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> str:
        return self._clock().isoformat(timespec="milliseconds")

    def _conn(self):
        return connect(self._settings.db_path)

    @property
    def backups(self) -> Path:
        return Path(self._settings.data_dir) / "backups" / "code"

    # --- criações ---------------------------------------------------------------------------------------------
    def creations(self, kind: str | None = None, archived: bool = False) -> list[dict[str, Any]]:
        if kind is not None and kind not in store.KINDS:
            raise AtelierError("Tipo inválido.")
        conn = self._conn()
        try:
            return store.listing(conn, kind, archived)
        finally:
            conn.close()

    def creation(self, creation_id: int) -> dict[str, Any]:
        conn = self._conn()
        try:
            item = store.get(conn, creation_id)
        finally:
            conn.close()
        if item is None:
            raise AtelierNotFound("Criação não encontrada.")
        return item

    def archive(self, creation_id: int) -> dict[str, Any]:
        conn = self._conn()
        try:
            with transaction(conn):
                if not store.archive(conn, creation_id):
                    raise AtelierNotFound("Criação não encontrada ou já arquivada.")
                log_audit(conn, "creation.archived", {"creation_id": creation_id, "by": "user"})
        finally:
            conn.close()
        return {"id": creation_id, "status": "archived"}

    # --- reações --------------------------------------------------------------------------------------------------
    def react(self, target: str, target_id: int, value: int, comment: str | None = None) -> dict[str, Any] | None:
        conn = self._conn()
        try:
            with transaction(conn):
                try:
                    return feedback.record(conn, target, target_id, value, comment, self._now())
                except feedback.FeedbackError as exc:
                    raise AtelierError(str(exc)) from None
        finally:
            conn.close()

    # --- propostas de código ---------------------------------------------------------------------------------------
    def proposals(self) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            return selfcode.listing(conn)
        finally:
            conn.close()

    def decide(self, proposal_id: int, action: str) -> dict[str, Any]:
        conn = self._conn()
        try:
            with transaction(conn):
                try:
                    if action == "apply":
                        return selfcode.apply(conn, self._settings.project_root, self.backups, proposal_id, self._now())
                    if action == "revert":
                        return selfcode.revert(conn, self._settings.project_root, proposal_id, self._now())
                    if action == "reject":
                        return selfcode.reject(conn, proposal_id, self._now())
                except LookupError as exc:
                    raise AtelierNotFound(str(exc)) from None
                except selfcode.SelfCodeError as exc:
                    if "desatualizada" in str(exc):   # o 'stale' precisa ficar gravado mesmo com o erro
                        conn.commit()
                    raise AtelierError(str(exc)) from None
            raise AtelierError("Ação inválida.")
        finally:
            conn.close()

    # --- lições ---------------------------------------------------------------------------------------------------
    def lessons(self) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            return lessons.listing(conn)
        finally:
            conn.close()

    def set_lesson(self, lesson_id: int, active: bool) -> dict[str, Any]:
        conn = self._conn()
        try:
            with transaction(conn):
                ok = (lessons.restore(conn, lesson_id, self._now()) if active
                      else lessons.retire(conn, lesson_id, "aposentada por ele", self._now()))
                if not ok:
                    raise AtelierNotFound("Lição não encontrada (ou já está assim).")
                log_audit(conn, "lesson.restored" if active else "lesson.retired", {"lesson_id": lesson_id, "by": "user"})
        finally:
            conn.close()
        return {"id": lesson_id, "status": "active" if active else "retired"}
