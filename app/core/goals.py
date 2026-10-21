"""Objetivos persistentes (Fase 2: só registro e estado; decomposição e execução ficam para a Fase 5).

Objetivos criados pelo usuário nascem ativos. Objetivos de origem "nym" nascem
como "proposed" e só entram no contexto do modelo depois de ativados pelo usuário.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.database import identity_repo as repo
from app.database.connection import connect, transaction
from app.database.repository import log_audit

log = logging.getLogger("nym.goals")

STATUSES = ("proposed", "active", "paused", "done", "cancelled")
MAX_GOAL_CHARS = 500


class GoalError(Exception):
    pass


class InvalidGoalInput(GoalError):
    pass


class GoalNotFoundError(GoalError):
    pass


def _check_priority(priority: Any) -> int:
    if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5:
        raise InvalidGoalInput("A prioridade deve ser um inteiro de 1 (mais alta) a 5.")
    return priority


def _check_description(description: Any) -> str:
    if not isinstance(description, str) or not description.strip():
        raise InvalidGoalInput("A descrição não pode ser vazia.")
    description = description.strip()
    if len(description) > MAX_GOAL_CHARS:
        raise InvalidGoalInput(f"A descrição excede {MAX_GOAL_CHARS} caracteres.")
    return description


class GoalService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def add_goal(self, description: str, priority: int = 3, origin: str = "user") -> dict[str, Any]:
        if origin not in ("user", "nym"):
            raise InvalidGoalInput("Origem inválida.")
        description = _check_description(description)
        priority = _check_priority(priority)
        status = "active" if origin == "user" else "proposed"
        conn = connect(self._settings.db_path)
        try:
            with transaction(conn):
                goal_id = repo.insert_goal(conn, description, origin, priority, status)
                log_audit(conn, "goal.created", {"goal_id": goal_id, "origin": origin, "status": status})
            return dict(repo.get_goal(conn, goal_id))
        finally:
            conn.close()

    def list_goals(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status not in STATUSES:
            raise InvalidGoalInput(f"Estado inválido: {status!r}.")
        conn = connect(self._settings.db_path)
        try:
            return [dict(r) for r in repo.list_goals(conn, status)]
        finally:
            conn.close()

    def update_goal(
        self, goal_id: int, *, status: str | None = None, priority: int | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        if status is not None and status not in STATUSES:
            raise InvalidGoalInput(f"Estado inválido: {status!r}.")
        if priority is not None:
            priority = _check_priority(priority)
        if description is not None:
            description = _check_description(description)
        conn = connect(self._settings.db_path)
        try:
            with transaction(conn):
                if repo.get_goal(conn, goal_id) is None:
                    raise GoalNotFoundError("Objetivo não encontrado.")
                repo.update_goal(conn, goal_id, status=status, priority=priority, description=description)
                log_audit(conn, "goal.updated", {"goal_id": goal_id, "status": status, "priority": priority})
            return dict(repo.get_goal(conn, goal_id))
        finally:
            conn.close()
