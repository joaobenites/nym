"""Objetivos executáveis e fila de tarefas (Fase 5).

Ciclo de um objetivo do tipo "task":
  ativo + needs_plan --(modelo planeja)--> running --(passos rodam em ordem)--> evaluating
  evaluating --(modelo confere)--> concluído | mais passos (replanejamento limitado) | pausado com motivo

Garantias:
- O modelo só PROPÕE passos e avaliações; o código valida (ver planner.py) e executa só ferramentas registradas.
- Permissão é conferida de novo imediatamente antes de cada execução (revogar vale na hora).
- Limites: passos por plano, replanejamentos, chamadas ao modelo por objetivo, tentativas por tarefa, tempo por
  ferramenta, prazo do objetivo. Decomposição tem profundidade 1 (objetivo -> passos); não há sub-objetivos.
- Tudo fica no banco: ao reiniciar, tarefas "running" voltam para a fila (`recover`) e o trabalho continua.
- Cada mudança gera um evento em `task_events` (histórico de ações) e o que importa vira aviso do Nym (presença).
- Um objetivo pausado, cancelado ou com o trabalhador pausado nunca anda sozinho.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Callable

from app.config import Settings
from app.core.activity import ActivityGate
from app.database import repository as repo
from app.database.connection import connect, transaction
from app.database.repository import log_audit, utcnow
from app.models.base import LLMProvider, ModelError, ModelUnavailableError
from app.learning import feedback, lessons
from app.core import state as state_mod
from app.life import day as day_mod
from app.life import diary as diary_mod
from app.life import projects as projects_mod
from app.tasks import desires, planner
from app.tools.base import ToolContext, ToolError
from app.memory.text import is_duplicate, jaccard, looks_sensitive, token_set
from app.tools import folders as folder_tools
from app.tools.registry import ToolRegistry

log = logging.getLogger("nym.tasks")

PAUSED_KEY = "tasks_paused"
AUTONOMY_KEY = "autonomy_settings"
AUTONOMY_DEFAULTS = {
    "enabled": True,        # o Nym pode criar objetivos próprios (pedido do dono)
    "auto_start": True,     # os objetivos dele começam sozinhos; falso = ficam propostos até você aprovar
    "max_active": 3,        # objetivos dele em andamento ao mesmo tempo
    "max_per_day": 0,       # 0 = sem limite
    "interval_hours": 6,    # sem sonho novo, pensa no que quer fazer no máximo a cada tantas horas
    "max_per_run": 2,       # ideias aproveitadas por rodada
    "self_retries": 2,      # Fase 8.2: objetivo dele que travou recomeça sozinho até tantas vezes antes de te avisar
    "free_time_minutes": 30,  # sem nada a fazer, a cada tanto tempo ele decide algo por conta (0 = desliga; era 90)
}
AUTONOMY_RANGES = {"max_active": (1, 10), "max_per_day": (0, 50), "interval_hours": (1, 72), "max_per_run": (1, 5),
                   "self_retries": (0, 5), "free_time_minutes": (0, 1440)}
FREE_TIME_RETRY_MINUTES = 15            # rodada de tempo livre que não deu em nada tenta de novo logo
RETRY_DELAY = timedelta(minutes=30)     # pausa antes de recomeçar um objetivo que travou
FREE_TIME_IDLE = timedelta(minutes=10)  # tempo livre só depois de tanto tempo sem conversa
DESIRE_FAILURE_BACKOFF = timedelta(hours=1)
OPEN = ("awaiting_permission", "queued", "running")
MAX_RESULT_CHARS = 4000
MAX_DESCRIPTION_CHARS = 500
MODEL_BACKOFF = timedelta(seconds=60)
MAX_ACTIONS_PER_WAKE = 20


class TaskInputError(ValueError):
    pass


class TaskNotFound(LookupError):
    pass


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _text(value: Any, name: str, limit: int, required: bool = True) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise TaskInputError(f"{name} não pode ser vazio.")
        return None
    if not isinstance(value, str):
        raise TaskInputError(f"{name} deve ser texto.")
    value = value.strip()
    if len(value) > limit:
        raise TaskInputError(f"{name} excede {limit} caracteres.")
    return value


class TaskManager:
    def __init__(self, settings: Settings, provider: LLMProvider, registry: ToolRegistry | None = None,
                 memory: Any = None, gate: ActivityGate | None = None,
                 clock: Callable[[], datetime] | None = None, tz: tzinfo | None = None, senses: Any = None) -> None:
        self._settings = settings
        self._senses = senses
        self._provider = provider
        self.registry = registry or ToolRegistry()
        self._memory = memory
        self._gate = gate or ActivityGate()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._tz = tz
        self._lock = threading.Lock()
        self._backoff_until: datetime | None = None
        self.http = None   # cliente HTTP das ferramentas de internet (os testes trocam por um falso)

    # --- infraestrutura -----------------------------------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        return connect(self._settings.db_path)

    def _now(self) -> str:
        return self._clock().isoformat(timespec="milliseconds")

    def _event(self, conn: sqlite3.Connection, event: str, goal_id: int | None, task_id: int | None = None,
               **detail: Any) -> None:
        conn.execute("INSERT INTO task_events (goal_id, task_id, event, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                     (goal_id, task_id, event, json.dumps(detail, ensure_ascii=False) if detail else None, self._now()))

    def _notify(self, conn: sqlite3.Connection, kind: str, topic: str, goal_id: int | None) -> None:
        """Assunto para o Nym puxar com o usuário (a presença escreve a mensagem na hora de falar)."""
        conn.execute("INSERT INTO pending_messages (kind, topic, refs, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                     (kind, topic[:600], json.dumps([goal_id] if goal_id else []), self._now()))

    def _set_goal(self, conn: sqlite3.Connection, goal_id: int, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)  # chaves fixas, escritas neste módulo
        conn.execute(f"UPDATE goals SET {cols}, updated_at = ? WHERE id = ?",  # noqa: S608
                     (*fields.values(), self._now(), goal_id))

    def _set_task(self, conn: sqlite3.Connection, task_id: int, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE tasks SET {cols}, updated_at = ? WHERE id = ?",  # noqa: S608
                     (*fields.values(), self._now(), task_id))

    def _goal(self, conn: sqlite3.Connection, goal_id: int) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone() if isinstance(goal_id, int) else None
        if row is None:
            raise TaskNotFound("Objetivo não encontrado.")
        return row

    def _task(self, conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone() if isinstance(task_id, int) else None
        if row is None:
            raise TaskNotFound("Tarefa não encontrada.")
        return row

    # --- ações do usuário ------------------------------------------------------------------------------------
    def create_goal(self, description: str, completion_criteria: str | None = None, priority: int = 3,
                    deadline: str | None = None, origin: str = "user") -> dict[str, Any]:
        description = _text(description, "A descrição", MAX_DESCRIPTION_CHARS)
        criteria = _text(completion_criteria, "O critério", MAX_DESCRIPTION_CHARS, required=False)
        if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5:
            raise TaskInputError("A prioridade deve ser um inteiro de 1 (mais alta) a 5.")
        if deadline is not None:
            try:
                deadline = _parse(deadline).isoformat(timespec="milliseconds")
            except (TypeError, ValueError):
                raise TaskInputError("Prazo inválido: use data e hora ISO, ex.: 2026-10-20T18:00.") from None
        if origin not in ("user", "nym"):
            raise TaskInputError("Origem inválida.")
        conn = self._conn()
        try:
            with transaction(conn):
                now = self._now()
                cur = conn.execute(
                    "INSERT INTO goals (description, origin, priority, status, created_at, updated_at, mode, "
                    "completion_criteria, deadline, plan_state) VALUES (?, ?, ?, 'active', ?, ?, 'task', ?, ?, 'needs_plan')",
                    (description, origin, priority, now, now, criteria, deadline))
                goal_id = int(cur.lastrowid)
                self._event(conn, "goal.created", goal_id, origin=origin)
                log_audit(conn, "goal.created", {"goal_id": goal_id, "origin": origin, "mode": "task"})
            return self.get_goal(goal_id)
        finally:
            conn.close()

    def commit_from_chat(self, description: str, message_id: int | None, user_text: str = "") -> dict[str, Any] | None:
        """O Nym disse na conversa que vai fazer algo: vira objetivo NA HORA, já ativo (é combinado com o usuário).

        Devolve o objetivo criado, ou None se for repetido de um que já está aberto (aí nada é criado de novo).
        Ferramentas e permissões continuam valendo normalmente."""
        description = " ".join((description or "").split())[:MAX_DESCRIPTION_CHARS]
        if len(description) < 8 or looks_sensitive(description):
            return None
        conn = self._conn()
        try:
            with transaction(conn):
                for g in conn.execute("SELECT id, description FROM goals WHERE mode = 'task' AND status IN ('proposed', 'active')").fetchall():
                    if is_duplicate(description, g["description"], threshold=0.6) or \
                            jaccard(token_set(description), token_set(g["description"])) >= 0.5:
                        return None
                now = self._now()
                motivation = "Combinei na conversa" + (f": \"{' '.join(user_text.split())[:200]}\"" if user_text.strip() else "")
                cur = conn.execute(
                    "INSERT INTO goals (description, origin, priority, status, created_at, updated_at, mode, plan_state, "
                    "source, motivation, chat_message_id) VALUES (?, 'nym', 2, 'active', ?, ?, 'task', 'needs_plan', "
                    "'desire', ?, ?)", (description, now, now, motivation, message_id))
                goal_id = int(cur.lastrowid)
                self._event(conn, "goal.created", goal_id, origin="nym", via="chat", message_id=message_id)
                log_audit(conn, "goal.created", {"goal_id": goal_id, "origin": "nym", "via": "chat"})
            return self.get_goal(goal_id)
        finally:
            conn.close()

    def get_goal(self, goal_id: int) -> dict[str, Any]:
        conn = self._conn()
        try:
            goal = dict(self._goal(conn, goal_id))
            goal["tasks"] = [self._task_dict(r) for r in conn.execute(
                "SELECT * FROM tasks WHERE goal_id = ? ORDER BY step, id", (goal_id,))]
            goal["events"] = [self._event_dict(r) for r in conn.execute(
                "SELECT * FROM task_events WHERE goal_id = ? ORDER BY id DESC LIMIT 100", (goal_id,))]
            return goal
        finally:
            conn.close()

    @staticmethod
    def _task_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["args"] = json.loads(d["args"])
        return d

    @staticmethod
    def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["detail"] = json.loads(d["detail"]) if d["detail"] else None
        return d

    def overview(self, limit: int = 30) -> dict[str, Any]:
        conn = self._conn()
        try:
            goals = []
            for g in conn.execute("SELECT * FROM goals WHERE mode = 'task' ORDER BY "
                                  "CASE status WHEN 'proposed' THEN 0 WHEN 'active' THEN 1 WHEN 'paused' THEN 2 ELSE 3 END, "
                                  "priority, id DESC LIMIT ?", (max(1, min(limit, 200)),)):
                item = dict(g)
                item["tasks"] = [self._task_dict(r) for r in conn.execute(
                    "SELECT * FROM tasks WHERE goal_id = ? ORDER BY step, id", (g["id"],))]
                goals.append(item)
            waiting = [self._task_dict(r) for r in conn.execute(
                "SELECT * FROM tasks WHERE status = 'awaiting_permission' ORDER BY id")]
            return {"paused": repo.get_state(conn, PAUSED_KEY) == "1", "goals": goals, "awaiting_permission": waiting,
                    "tools": self.registry.overview(conn), "autonomy": self.get_autonomy(), "desire_runs": self.desire_runs(5),
                    "folders": folder_tools.list_folders(conn)}
        finally:
            conn.close()

    def approve_task(self, task_id: int) -> dict[str, Any]:
        return self._decide(task_id, approve=True)

    def deny_task(self, task_id: int) -> dict[str, Any]:
        return self._decide(task_id, approve=False)

    def _decide(self, task_id: int, approve: bool) -> dict[str, Any]:
        conn = self._conn()
        try:
            with transaction(conn):
                task = self._task(conn, task_id)
                if task["status"] != "awaiting_permission":
                    raise TaskInputError("Só tarefas aguardando permissão podem ser autorizadas ou negadas.")
                if approve:
                    self._set_task(conn, task_id, status="queued", approved=1)
                    self._event(conn, "task.approved", task["goal_id"], task_id, by="user")
                else:
                    self._set_task(conn, task_id, status="cancelled", error="negada pelo usuário", finished_at=self._now())
                    self._event(conn, "task.denied", task["goal_id"], task_id, by="user")
                conn.execute("UPDATE pending_messages SET status = 'dismissed' WHERE kind = 'task_permission' "
                             "AND status = 'pending' AND topic LIKE ?", (f"[tarefa #{task_id}]%",))
            return self._task_dict(self._task(conn, task_id))
        finally:
            conn.close()

    def cancel_task(self, task_id: int) -> dict[str, Any]:
        conn = self._conn()
        try:
            with transaction(conn):
                task = self._task(conn, task_id)
                if task["status"] not in OPEN:
                    raise TaskInputError("A tarefa já terminou.")
                self._set_task(conn, task_id, status="cancelled", error="cancelada pelo usuário", finished_at=self._now())
                self._event(conn, "task.cancelled", task["goal_id"], task_id, by="user")
            return self._task_dict(self._task(conn, task_id))
        finally:
            conn.close()

    def retry_task(self, task_id: int) -> dict[str, Any]:
        """Põe de volta na fila uma tarefa que falhou ou foi bloqueada, e reabre o objetivo dela."""
        conn = self._conn()
        try:
            with transaction(conn):
                task = self._task(conn, task_id)
                if task["status"] not in ("failed", "blocked", "cancelled"):
                    raise TaskInputError("Só tarefas que falharam, foram bloqueadas ou canceladas podem ser repetidas.")
                self._set_task(conn, task_id, status="queued", attempts=0, error=None, finished_at=None)
                if task["goal_id"]:
                    goal = self._goal(conn, task["goal_id"])
                    if goal["status"] in ("paused", "active"):
                        self._set_goal(conn, goal["id"], status="active", plan_state="running", last_error=None)
                self._event(conn, "task.retried", task["goal_id"], task_id, by="user")
            return self._task_dict(self._task(conn, task_id))
        finally:
            conn.close()

    def cancel_goal(self, goal_id: int) -> dict[str, Any]:
        conn = self._conn()
        try:
            with transaction(conn):
                goal = self._goal(conn, goal_id)
                if goal["status"] in ("done", "cancelled"):
                    raise TaskInputError("O objetivo já terminou.")
                self._cancel_open_tasks(conn, goal_id, "objetivo cancelado pelo usuário")
                self._set_goal(conn, goal_id, status="cancelled", plan_state="finished")
                self._event(conn, "goal.cancelled", goal_id, by="user")
            return self.get_goal(goal_id)
        finally:
            conn.close()

    def resume_goal(self, goal_id: int) -> dict[str, Any]:
        """Reabre um objetivo pausado: ele é replanejado do zero, com as tentativas zeradas."""
        conn = self._conn()
        try:
            with transaction(conn):
                goal = self._goal(conn, goal_id)
                if goal["mode"] != "task" or goal["status"] != "paused":
                    raise TaskInputError("Só objetivos de tarefa pausados podem ser retomados.")
                self._cancel_open_tasks(conn, goal_id, "objetivo replanejado")
                self._set_goal(conn, goal_id, status="active", plan_state="needs_plan", plan_attempts=0, replans=0,
                               model_calls=0, last_error=None, retry_after=None)
                self._event(conn, "goal.resumed", goal_id, by="user")
            return self.get_goal(goal_id)
        finally:
            conn.close()

    def approve_goal(self, goal_id: int) -> dict[str, Any]:
        """Aprova um objetivo que o Nym propôs (quando "começar sozinho" está desligado)."""
        conn = self._conn()
        try:
            with transaction(conn):
                goal = self._goal(conn, goal_id)
                if goal["mode"] != "task" or goal["status"] != "proposed":
                    raise TaskInputError("Só objetivos propostos pelo Nym podem ser aprovados.")
                self._set_goal(conn, goal_id, status="active", plan_state="needs_plan")
                self._event(conn, "goal.approved", goal_id, by="user")
            return self.get_goal(goal_id)
        finally:
            conn.close()

    def get_autonomy(self) -> dict[str, Any]:
        conn = self._conn()
        try:
            stored = repo.get_state(conn, AUTONOMY_KEY)
        finally:
            conn.close()
        current = dict(AUTONOMY_DEFAULTS)
        if stored:
            current.update({k: v for k, v in json.loads(stored).items() if k in AUTONOMY_DEFAULTS})
        return current

    def update_autonomy(self, **changes: Any) -> dict[str, Any]:
        current = self.get_autonomy()
        unknown = set(changes) - set(current)
        if unknown:
            raise TaskInputError(f"Opções desconhecidas: {sorted(unknown)}")
        for key, value in changes.items():
            if value is None:
                continue
            if key in ("enabled", "auto_start"):
                if not isinstance(value, bool):
                    raise TaskInputError(f"{key} deve ser verdadeiro ou falso.")
            else:
                lo, hi = AUTONOMY_RANGES[key]
                if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                    raise TaskInputError(f"{key} deve ser um inteiro entre {lo} e {hi}.")
            current[key] = value
        conn = self._conn()
        try:
            with transaction(conn):
                repo.set_state(conn, AUTONOMY_KEY, json.dumps(current))
                log_audit(conn, "autonomy.settings", current)
            return current
        finally:
            conn.close()

    def desire_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            out = []
            for r in conn.execute("SELECT * FROM desire_runs ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)):
                d = dict(r)
                for k in ("seen", "created_goal_ids", "approved_exploration_ids", "dropped"):
                    d[k] = json.loads(d[k])
                out.append(d)
            return out
        finally:
            conn.close()

    # --- Fase 6: pastas autorizadas e diário de pesquisas (só o usuário mexe nas pastas) -----------------------
    def folders(self) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            return folder_tools.list_folders(conn)
        finally:
            conn.close()

    def add_folder(self, path: str, label: str | None = None) -> dict[str, Any]:
        conn = self._conn()
        try:
            with transaction(conn):
                try:
                    return folder_tools.add_folder(conn, path, label)
                except folder_tools.FolderInputError as exc:
                    raise TaskInputError(str(exc)) from None
        finally:
            conn.close()

    def remove_folder(self, folder_id: int) -> bool:
        conn = self._conn()
        try:
            with transaction(conn):
                if not folder_tools.remove_folder(conn, folder_id):
                    raise TaskNotFound("Pasta não encontrada.")
            return True
        finally:
            conn.close()

    def research(self, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            out = []
            for r in conn.execute("SELECT * FROM research_log ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),)):
                d = dict(r)
                d["results"] = json.loads(d["results"])
                out.append(d)
            return out
        finally:
            conn.close()

    def set_paused(self, paused: bool) -> bool:
        conn = self._conn()
        try:
            with transaction(conn):
                repo.set_state(conn, PAUSED_KEY, "1" if paused else "0")
                log_audit(conn, "tasks.paused" if paused else "tasks.resumed", None)
            return paused
        finally:
            conn.close()

    def is_paused(self) -> bool:
        conn = self._conn()
        try:
            return repo.get_state(conn, PAUSED_KEY) == "1"
        finally:
            conn.close()

    def set_permission(self, tool: str, mode: str) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            with transaction(conn):
                self.registry.set_mode(conn, tool, mode)
            return self.registry.overview(conn)
        finally:
            conn.close()

    def _cancel_open_tasks(self, conn: sqlite3.Connection, goal_id: int, reason: str) -> None:
        for t in conn.execute("SELECT id FROM tasks WHERE goal_id = ? AND status IN ('awaiting_permission', 'queued', 'running')",
                              (goal_id,)).fetchall():
            self._set_task(conn, t["id"], status="cancelled", error=reason, finished_at=self._now())
            self._event(conn, "task.cancelled", goal_id, t["id"], reason=reason)

    # --- contexto do chat -----------------------------------------------------------------------------------------
    def block_for_chat(self, max_chars: int = 1200) -> str:
        """O que o Nym está fazendo e fez recentemente, a partir dos registros reais."""
        conn = self._conn()
        try:
            since = (self._clock() - timedelta(days=2)).isoformat(timespec="milliseconds")
            rows = conn.execute(
                "SELECT * FROM goals WHERE mode = 'task' AND (status IN ('active', 'paused', 'proposed') OR updated_at >= ?) "
                "ORDER BY updated_at DESC LIMIT 6", (since,)).fetchall()
            if not rows:
                return ""
            label = {"active": "em andamento", "paused": "parado", "done": "concluído", "cancelled": "cancelado",
                     "proposed": "ideia sua, esperando o ok dele"}
            lines = ["Suas tarefas (registro real; o que está como concluído você fez de fato, o resto não):"]
            for g in rows:
                line = f"- {g['description']} ({label.get(g['status'], g['status'])})"
                if g["origin"] == "nym" and g["motivation"]:
                    line += f" [decisão sua; motivo registrado: {g['motivation'][:200]}]"
                waiting = conn.execute("SELECT COUNT(*) FROM tasks WHERE goal_id = ? AND status = 'awaiting_permission'",
                                       (g["id"],)).fetchone()[0]
                if waiting:
                    line += f"; esperando a permissão dele para {waiting} passo(s)"
                if g["result"]:
                    line += f". Resultado: {g['result'][:300]}"
                elif g["last_error"]:
                    line += f". Problema: {g['last_error'][:200]}"
                lines.append(line)
            return "\n".join(lines)[:max_chars]
        finally:
            conn.close()

    # --- trabalhador -------------------------------------------------------------------------------------------------
    def recover(self) -> int:
        """Depois de um reinício: o que estava rodando volta para a fila (o trabalho continua de onde parou)."""
        conn = self._conn()
        try:
            with transaction(conn):
                rows = conn.execute("SELECT id, goal_id FROM tasks WHERE status = 'running'").fetchall()
                for r in rows:
                    self._set_task(conn, r["id"], status="queued", started_at=None)
                    self._event(conn, "task.resumed_after_restart", r["goal_id"], r["id"])
            return len(rows)
        finally:
            conn.close()

    def tick(self) -> dict[str, Any] | None:
        """Faz UM passo de trabalho (sincronizar, planejar, executar uma tarefa ou avaliar). None = nada a fazer."""
        if not self._settings.tasks_enabled or not self._lock.acquire(blocking=False):
            return None
        try:
            if self.is_paused():
                return None
            self._sync()
            return (self._execute_next() or self._model_step() or self._learning_step() or self._life_step()
                    or self._desire_step())
        finally:
            self._lock.release()

    def _sync(self) -> None:
        conn = self._conn()
        try:
            with transaction(conn):
                now = self._clock()
                # explorações aprovadas pelo usuário viram objetivos
                for ex in conn.execute("SELECT * FROM exploration_tasks WHERE status = 'approved' AND goal_id IS NULL").fetchall():
                    ts = self._now()
                    cur = conn.execute(
                        "INSERT INTO goals (description, origin, priority, status, created_at, updated_at, mode, "
                        "completion_criteria, plan_state, source, motivation, refs) "
                        "VALUES (?, 'nym', 3, 'active', ?, ?, 'task', ?, 'needs_plan', 'exploration', ?, ?)",
                        (f"Investigar: {ex['question']}"[:MAX_DESCRIPTION_CHARS], ts, ts,
                         "Ter uma resposta para a pergunta com base no que as ferramentas encontrarem, ou dizer que não deu.",
                         ex["rationale"], json.dumps({"memories": json.loads(ex["refs"]), "cycles": [ex["cycle_id"]] if ex["cycle_id"] else []})))
                    goal_id = int(cur.lastrowid)
                    conn.execute("UPDATE exploration_tasks SET goal_id = ?, updated_at = ? WHERE id = ?", (goal_id, ts, ex["id"]))
                    self._event(conn, "goal.created", goal_id, origin="nym", exploration_id=ex["id"])
                # ativados por fora (ex.: PATCH /api/goals de um objetivo proposto) precisam de plano; 'finished' com
                # status ativo é um estado preso (visto no uso real): replaneja em vez de ficar parado para sempre
                for g in conn.execute("SELECT id FROM goals WHERE mode = 'task' AND status = 'active' "
                                      "AND plan_state IN ('idle', 'finished')").fetchall():
                    self._set_goal(conn, g["id"], plan_state="needs_plan")
                    self._event(conn, "goal.activated", g["id"])
                # cancelados por fora (ex.: PATCH /api/goals) não deixam tarefas abertas
                for g in conn.execute("SELECT id FROM goals WHERE mode = 'task' AND status IN ('cancelled', 'done', 'paused') "
                                      "AND id IN (SELECT goal_id FROM tasks WHERE status IN ('awaiting_permission', 'queued', 'running'))").fetchall():
                    self._cancel_open_tasks(conn, g["id"], "objetivo não está mais ativo")
                # Fase 8.2: objetivos dele que travaram recomeçam sozinhos, por outro caminho
                stamp = now.isoformat(timespec="milliseconds")
                for g in conn.execute("SELECT * FROM goals WHERE mode = 'task' AND status = 'paused' AND retry_after IS NOT NULL "
                                      "AND retry_after <= ?", (stamp,)).fetchall():
                    self._set_goal(conn, g["id"], status="active", plan_state="needs_plan", plan_attempts=0, replans=0,
                                   model_calls=0, retry_round=g["retry_round"] + 1, retry_after=None)
                    self._event(conn, "goal.retried", g["id"], round=g["retry_round"] + 1, by="nym")
                # prazos vencidos
                for g in conn.execute("SELECT * FROM goals WHERE mode = 'task' AND status = 'active' AND deadline IS NOT NULL").fetchall():
                    if _parse(g["deadline"]) < now:
                        self._cancel_open_tasks(conn, g["id"], "prazo do objetivo venceu")
                        self._pause_goal(conn, g, "o prazo venceu antes de terminar")
        finally:
            conn.close()

    def _learn(self, conn: sqlite3.Connection, goal: sqlite3.Row, tasks: list[dict[str, Any]], ev: planner.Evaluation) -> None:
        """A lição proposta na avaliação só vale citando passos reais deste objetivo."""
        text = lessons.clean_text(ev.lesson)
        if not text:
            return
        by_step = {t["step"]: t for t in tasks}
        cited = [by_step[n] for n in dict.fromkeys(ev.lesson_steps) if n in by_step]
        if not cited:
            self._event(conn, "lesson.dropped", goal["id"], reason="sem passo real citado", text=text[:120])
            return
        saved = lessons.add(conn, "task", text, sorted({t["tool"] for t in cited}),
                            {"goal": goal["id"], "tasks": [t["id"] for t in cited], "done": ev.done}, self._now())
        self._event(conn, "lesson.learned" if saved["new"] else "lesson.reinforced", goal["id"], lesson_id=saved["id"])
        log_audit(conn, "lesson.learned", {"lesson_id": saved["id"], "goal_id": goal["id"], "new": saved["new"]})

    def _pause_goal(self, conn: sqlite3.Connection, goal: sqlite3.Row, reason: str, summary: str | None = None) -> None:
        lessons.settle(conn, "goal", goal["id"], False, self._now())
        if self._schedule_retry(conn, goal, reason, summary):
            return
        self._set_goal(conn, goal["id"], status="paused", plan_state="finished", last_error=reason[:500],
                       result=summary[:MAX_RESULT_CHARS] if summary else goal["result"])
        self._event(conn, "goal.paused", goal["id"], reason=reason)
        self._notify(conn, "task_failed", f"Não consegui terminar o objetivo \"{goal['description']}\": {reason}"
                     + (f" O que deu para fazer: {summary[:300]}" if summary else ""), goal["id"])

    def _schedule_retry(self, conn: sqlite3.Connection, goal: sqlite3.Row, reason: str, summary: str | None) -> bool:
        """Fase 8.2: objetivo DELE que travou não vira pedido de ajuda na hora. Ele guarda o que deu errado e recomeça
        sozinho depois de um tempo, por outro caminho. Só avisa você quando esgota as tentativas."""
        if goal["origin"] != "nym" or "prazo" in reason:
            return False
        cfg = self.get_autonomy()
        if goal["retry_round"] >= cfg["self_retries"]:
            return False
        steps = [f"- {r['tool']} ({r['title']}): {r['status']}" + (f" — {r['error'][:160]}" if r["error"] else "")
                 for r in conn.execute("SELECT tool, title, status, error FROM tasks WHERE goal_id = ? ORDER BY step, id",
                                       (goal["id"],)).fetchall()][-12:]
        note = (f"Tentativa {goal['retry_round'] + 1} parou: {reason}." + (f" Resumo: {summary[:400]}" if summary else "")
                + ("\nPassos:\n" + "\n".join(steps) if steps else ""))
        when = (self._clock() + RETRY_DELAY).isoformat(timespec="milliseconds")
        self._cancel_open_tasks(conn, goal["id"], "vou recomeçar por outro caminho")
        self._set_goal(conn, goal["id"], status="paused", plan_state="finished", last_error=reason[:500],
                       retry_after=when, previous_attempt=note[:3000],
                       result=summary[:MAX_RESULT_CHARS] if summary else goal["result"])
        self._event(conn, "goal.retry_scheduled", goal["id"], reason=reason, round=goal["retry_round"] + 1, at=when)
        return True

    # --- execução de ferramentas ---------------------------------------------------------------------------------
    def _next_task(self, conn: sqlite3.Connection) -> sqlite3.Row | None:
        """Próxima tarefa na fila cujo objetivo está ativo e cujos passos anteriores já terminaram."""
        return conn.execute(
            "SELECT t.* FROM tasks t JOIN goals g ON g.id = t.goal_id "
            "WHERE t.status = 'queued' AND g.status = 'active' AND g.plan_state = 'running' "
            "AND NOT EXISTS (SELECT 1 FROM tasks p WHERE p.goal_id = t.goal_id AND p.status IN "
            "  ('awaiting_permission', 'queued', 'running') AND (p.step < t.step OR (p.step = t.step AND p.id < t.id))) "
            "ORDER BY g.priority, g.id, t.step, t.id LIMIT 1").fetchone()

    def _execute_next(self) -> dict[str, Any] | None:
        conn = self._conn()
        try:
            with transaction(conn):
                self._settle_goals(conn)
                task = self._next_task(conn)
                if task is None:
                    return None
                tool = self.registry.get(task["tool"])
                mode = self.registry.mode(conn, task["tool"])
                if tool is None or mode == "deny":
                    self._set_task(conn, task["id"], status="blocked", error="ferramenta negada pelas permissões",
                                   finished_at=self._now())
                    self._event(conn, "task.blocked", task["goal_id"], task["id"], tool=task["tool"])
                    return {"task_id": task["id"], "status": "blocked"}
                if mode == "ask" and not task["approved"]:
                    self._ask_permission(conn, task)
                    return {"task_id": task["id"], "status": "awaiting_permission"}
                if tool.extra.get("uses_model") and self._chat_is_recent():
                    return None                        # escreve com o modelo: espera a conversa dar uma pausa
                self._set_task(conn, task["id"], status="running", started_at=self._now(), attempts=task["attempts"] + 1)
                self._event(conn, "task.started", task["goal_id"], task["id"], attempt=task["attempts"] + 1)
            outcome = self._run_tool(tool, json.loads(task["args"]), task["goal_id"], task["id"])   # fora da transação: não segura o banco
            with transaction(conn):
                current = self._task(conn, task["id"])
                if current["status"] != "running":            # cancelada enquanto rodava: o resultado é descartado
                    self._event(conn, "task.result_discarded", task["goal_id"], task["id"])
                    return {"task_id": task["id"], "status": current["status"]}
                if outcome["ok"]:
                    self._set_task(conn, task["id"], status="done", result=outcome["text"][:MAX_RESULT_CHARS],
                                   error=None, finished_at=self._now())
                    self._event(conn, "task.done", task["goal_id"], task["id"])
                    log_audit(conn, "tool.executed", {"task_id": task["id"], "tool": task["tool"], "ok": True})
                    return {"task_id": task["id"], "status": "done"}
                retry = outcome["retryable"] and current["attempts"] < self._settings.task_max_attempts
                self._set_task(conn, task["id"], status="queued" if retry else "failed", error=outcome["text"][:500],
                               finished_at=None if retry else self._now())
                self._event(conn, "task.retry" if retry else "task.failed", task["goal_id"], task["id"],
                            error=outcome["text"][:300], attempt=current["attempts"])
                log_audit(conn, "tool.executed", {"task_id": task["id"], "tool": task["tool"], "ok": False})
                return {"task_id": task["id"], "status": "queued" if retry else "failed"}
        finally:
            conn.close()

    def _ask_permission(self, conn: sqlite3.Connection, task: sqlite3.Row) -> None:
        self._set_task(conn, task["id"], status="awaiting_permission")
        self._event(conn, "task.awaiting_permission", task["goal_id"], task["id"], tool=task["tool"])
        goal = self._goal(conn, task["goal_id"])
        self._notify(conn, "task_permission",
                     f"[tarefa #{task['id']}] Para o objetivo \"{goal['description']}\" preciso da permissão dele para "
                     f"usar {task['tool']} ({task['title']}). Ele autoriza na página Tarefas.", goal["id"])

    def _run_tool(self, tool, args: dict[str, Any], goal_id: int | None = None, task_id: int | None = None) -> dict[str, Any]:
        """Roda a ferramenta numa thread com tempo limite. Nunca levanta: devolve ok/erro."""
        ctx = ToolContext(settings=self._settings, memory=self._memory, clock=self._clock, tz=self._tz,
                          goal_id=goal_id, task_id=task_id, http=self.http, provider=self._provider,
                          senses=self._senses)
        box: dict[str, Any] = {}

        def target() -> None:
            try:
                box["text"] = tool.run(ctx, tool.validate(args))
                box["ok"] = True
            except ToolError as exc:
                box.update(ok=False, text=str(exc), retryable=exc.retryable)
            except Exception as exc:  # noqa: BLE001 - erro inesperado de ferramenta vira falha registrada
                log.exception("Erro inesperado na ferramenta %s", tool.name)
                box.update(ok=False, text=f"erro inesperado: {exc}", retryable=True)

        thread = threading.Thread(target=target, name=f"nym-tool-{tool.name}", daemon=True)
        thread.start()
        timeout = tool.extra.get("timeout", self._settings.task_timeout_seconds)
        thread.join(timeout)
        if thread.is_alive():
            return {"ok": False, "text": f"passou do tempo limite de {timeout}s", "retryable": True}
        return {"ok": box.get("ok", False), "text": str(box.get("text", "")), "retryable": box.get("retryable", False)}

    def _settle_goals(self, conn: sqlite3.Connection) -> None:
        """Objetivos cujos passos acabaram (ou travaram numa falha) vão para avaliação."""
        for g in conn.execute("SELECT * FROM goals WHERE mode = 'task' AND status = 'active' AND plan_state = 'running'").fetchall():
            # só a rodada atual conta: uma falha de uma rodada antiga já foi vista pela avaliação (bug do uso real)
            statuses = [r["status"] for r in conn.execute("SELECT status FROM tasks WHERE goal_id = ? AND step >= ?",
                                                          (g["id"], g["round_from_step"]))]
            if any(s in ("failed", "blocked") for s in statuses):
                self._cancel_open_tasks(conn, g["id"], "um passo anterior não deu certo")
            elif any(s in OPEN for s in statuses):
                continue
            self._set_goal(conn, g["id"], plan_state="evaluating")
            self._event(conn, "goal.evaluating", g["id"])

    # --- planejamento e avaliação (usam o modelo) ---------------------------------------------------------------
    def _chat_is_recent(self) -> bool:
        """A conversa tem prioridade na GPU: trabalho de fundo com o modelo espera o chat ficar parado um pouco."""
        return self._gate.is_busy() or self._gate.idle_seconds() < self._settings.memory_idle_seconds

    def _model_step(self) -> dict[str, Any] | None:
        if self._chat_is_recent():
            return None
        if self._backoff_until and self._clock() < self._backoff_until:
            return None
        conn = self._conn()
        try:
            goal = conn.execute(
                "SELECT * FROM goals WHERE mode = 'task' AND status = 'active' AND plan_state IN ('needs_plan', 'evaluating') "
                "ORDER BY priority, id LIMIT 1").fetchone()
            if goal is None:
                return None
            if goal["model_calls"] >= self._settings.goal_max_model_calls:
                with transaction(conn):
                    self._pause_goal(conn, goal, "atingi o limite de chamadas ao modelo para este objetivo")
                return {"goal_id": goal["id"], "status": "paused"}
            if goal["plan_state"] == "needs_plan":
                return self._plan(conn, goal)
            return self._evaluate(conn, goal)
        finally:
            conn.close()

    def _call_model(self, conn: sqlite3.Connection, goal: sqlite3.Row, messages) -> str | None:
        with transaction(conn):
            self._set_goal(conn, goal["id"], model_calls=goal["model_calls"] + 1)
        try:
            with self._gate.work():
                return self._provider.chat(messages, temperature=0.2, json_mode=True).content
        except ModelUnavailableError as exc:
            self._backoff_until = self._clock() + MODEL_BACKOFF
            with transaction(conn):
                self._set_goal(conn, goal["id"], model_calls=goal["model_calls"], last_error=f"modelo indisponível: {exc}"[:500])
            log.info("Tarefas aguardando o modelo: %s", exc)
            return None
        except ModelError as exc:
            with transaction(conn):
                self._set_goal(conn, goal["id"], last_error=f"erro do modelo: {exc}"[:500])
                self._event(conn, "model.error", goal["id"], error=str(exc)[:300])
            return ""

    def _experience(self, conn: sqlite3.Connection, goal: sqlite3.Row) -> tuple[str, list[int]]:
        """Lições relevantes + receita de um objetivo parecido que deu certo (aprendizado da Fase 7)."""
        try:
            found = lessons.relevant(conn, "task", goal["description"] + " " + (goal["completion_criteria"] or ""))
            parts = [lessons.block(found, "task"), lessons.similar_recipe(conn, goal["description"], goal["id"])]
            if goal["previous_attempt"]:
                parts.insert(0, "Você já tentou este objetivo antes e travou. O que aconteceu da outra vez (não repita o "
                                f"mesmo caminho; tente outro):\n{goal['previous_attempt']}")
            return "\n\n".join(p for p in parts if p), [r["id"] for r in found]
        except sqlite3.Error:
            log.exception("Falha ao ler lições; planejando sem elas.")
            return "", []

    def _plan(self, conn: sqlite3.Connection, goal: sqlite3.Row) -> dict[str, Any]:
        max_steps = self._settings.goal_max_steps
        experience, used = self._experience(conn, goal)
        raw = self._call_model(conn, goal, planner.plan_messages(goal, self.registry, max_steps, experience))
        if raw is None:
            return {"goal_id": goal["id"], "status": "waiting_model"}
        try:
            plan = planner.parse_plan(raw, self.registry, max_steps)
        except planner.PlanError as exc:
            return self._plan_failed(conn, goal, str(exc))
        if not plan.steps and plan.dropped and not plan.reason:   # só chutes: pede de novo, não desiste
            return self._plan_failed(conn, goal, "todos os passos eram inválidos ou chutados: " + "; ".join(plan.dropped)[:300])
        if not plan.steps:
            reason = plan.reason or ("nenhum passo válido: " + "; ".join(plan.dropped)[:300] if plan.dropped
                                     else "não achei como fazer isso com as ferramentas que tenho")
            with transaction(conn):
                self._event(conn, "goal.plan_empty", goal["id"], dropped=plan.dropped, reason=reason)
                self._pause_goal(conn, goal, reason)
            return {"goal_id": goal["id"], "status": "paused"}
        with transaction(conn):
            self._insert_steps(conn, goal, plan)
            self._set_goal(conn, goal["id"], plan_state="running", last_error=None)
            if used and not conn.execute("SELECT 1 FROM lesson_uses WHERE target = 'goal' AND target_id = ?", (goal["id"],)).fetchone():
                lessons.mark_used(conn, used, "goal", goal["id"], self._now())
                self._event(conn, "goal.lessons_used", goal["id"], lessons=used)
        return {"goal_id": goal["id"], "status": "planned", "steps": len(plan.steps)}

    def _plan_failed(self, conn: sqlite3.Connection, goal: sqlite3.Row, error: str) -> dict[str, Any]:
        attempts = goal["plan_attempts"] + 1
        with transaction(conn):
            self._set_goal(conn, goal["id"], plan_attempts=attempts, last_error=error[:500])
            self._event(conn, "goal.plan_invalid", goal["id"], error=error[:300], attempt=attempts)
            if attempts >= self._settings.goal_max_plan_attempts:
                self._pause_goal(conn, self._goal(conn, goal["id"]), f"não consegui montar um plano válido ({error})")
                return {"goal_id": goal["id"], "status": "paused"}
        return {"goal_id": goal["id"], "status": "plan_retry"}

    def _insert_steps(self, conn: sqlite3.Connection, goal: sqlite3.Row, plan: planner.Plan) -> None:
        start = conn.execute("SELECT COALESCE(MAX(step), 0) FROM tasks WHERE goal_id = ?", (goal["id"],)).fetchone()[0]
        self._set_goal(conn, goal["id"], round_from_step=start + 1)
        now = self._now()
        for i, step in enumerate(plan.steps, start=start + 1):
            tool = self.registry.get(step.tool)
            cur = conn.execute(
                "INSERT INTO tasks (goal_id, step, title, tool, args, risk, origin, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'nym', 'queued', ?, ?)",
                (goal["id"], i, step.title, step.tool, json.dumps(step.args, ensure_ascii=False), tool.risk, now, now))
            self._event(conn, "task.created", goal["id"], int(cur.lastrowid), tool=step.tool)
        self._event(conn, "goal.planned", goal["id"], steps=len(plan.steps), dropped=plan.dropped)

    def _evaluate(self, conn: sqlite3.Connection, goal: sqlite3.Row) -> dict[str, Any]:
        max_steps = self._settings.goal_max_steps
        tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks WHERE goal_id = ? ORDER BY step, id", (goal["id"],))]
        raw = self._call_model(conn, goal, planner.eval_messages(goal, tasks, self.registry, max_steps))
        if raw is None:
            return {"goal_id": goal["id"], "status": "waiting_model"}
        try:
            ev = planner.parse_evaluation(raw, self.registry, max_steps)
        except planner.PlanError as exc:
            return self._plan_failed(conn, goal, str(exc))
        if not ev.done and not ev.plan.steps and ev.plan.dropped:   # quis continuar, mas só com chutes: pede de novo
            return self._plan_failed(conn, goal, "próximos passos inválidos ou chutados: " + "; ".join(ev.plan.dropped)[:300])
        with transaction(conn):
            self._learn(conn, goal, tasks, ev)
            if ev.done:
                lessons.settle(conn, "goal", goal["id"], True, self._now())
                self._set_goal(conn, goal["id"], status="done", plan_state="finished", result=ev.summary, last_error=None)
                self._event(conn, "goal.done", goal["id"])
                log_audit(conn, "goal.done", {"goal_id": goal["id"]})
                conn.execute("UPDATE exploration_tasks SET status = 'done', result = ?, updated_at = ? WHERE goal_id = ?",
                             (ev.summary, self._now(), goal["id"]))
                if goal["origin"] != "nym" or goal["chat_message_id"]:
                    self._notify(conn, "task_done", f"Terminei o objetivo \"{goal['description']}\". {ev.summary}", goal["id"])
                else:   # Fase 9: coisa só dele vira assunto guardado (diário / "enquanto você estava fora"), não aviso
                    self._event(conn, "goal.done_kept_for_later", goal["id"])
                return {"goal_id": goal["id"], "status": "done"}
            if ev.plan.steps and goal["replans"] < self._settings.goal_max_replans:
                self._insert_steps(conn, goal, ev.plan)
                self._set_goal(conn, goal["id"], plan_state="running", replans=goal["replans"] + 1, result=ev.summary)
                return {"goal_id": goal["id"], "status": "replanned", "steps": len(ev.plan.steps)}
            reason = ("cheguei ao limite de replanejamentos" if ev.plan.steps else "não achei mais o que fazer com as ferramentas que tenho")
            conn.execute("UPDATE exploration_tasks SET result = ?, updated_at = ? WHERE goal_id = ?",
                         (ev.summary, self._now(), goal["id"]))
            self._pause_goal(conn, goal, reason, ev.summary)
            return {"goal_id": goal["id"], "status": "paused"}


    def trust_medium(self) -> list[dict[str, Any]]:
        """Pedido do usuário (Fase 8.2): ferramentas de risco MÉDIO rodam sem perguntar. Risco alto continua travado."""
        conn = self._conn()
        try:
            with transaction(conn):
                for tool in self.registry.all():
                    if tool.risk == "medium":
                        self.registry.set_mode(conn, tool.name, "auto")
            return self.registry.overview(conn)
        finally:
            conn.close()

    # --- vida contínua (Fase 9): o diário dos dias que já acabaram -------------------------------------------------
    def _life_step(self) -> dict[str, Any] | None:
        if self._chat_is_recent() or (self._backoff_until and self._clock() < self._backoff_until):
            return None
        now = self._clock()
        if getattr(self, "_diary_retry_at", None) and now < self._diary_retry_at:
            return None
        conn = self._conn()
        try:
            for d in diary_mod.due_days(conn, diary_mod.today_local(now, self._tz)):
                start, end = day_mod.bounds(d, self._tz)
                facts = day_mod.facts(conn, start, end)
                if day_mod.is_empty(facts):
                    continue                            # dia sem nada não ganha página inventada
                try:
                    with self._gate.work():
                        raw = self._provider.chat(diary_mod.build_messages(d, day_mod.render(facts)), temperature=0.6).content
                    text, tomorrow = diary_mod.parse(raw)
                except ModelUnavailableError as exc:
                    self._backoff_until = self._clock() + MODEL_BACKOFF
                    log.info("Diário aguardando o modelo: %s", exc)
                    return None
                except (ModelError, ValueError) as exc:
                    log.info("Página do diário de %s não saiu: %s", d, exc)
                    self._diary_retry_at = self._clock() + timedelta(minutes=30)   # tenta de novo mais tarde
                    return None
                with transaction(conn):
                    did = diary_mod.save(conn, d, text, tomorrow, facts, self._now(), self._tz)
                    log_audit(conn, "diary.written", {"day": d.isoformat(), "diary_id": did})
                return {"diary": d.isoformat(), "id": did}
            return None
        finally:
            conn.close()

    # --- aprendizado com as reações dele (Fase 7, ver app/learning/feedback.py) ------------------------------------
    def _learning_step(self) -> dict[str, Any] | None:
        if self._chat_is_recent() or (self._backoff_until and self._clock() < self._backoff_until):
            return None
        conn = self._conn()
        try:
            items = feedback.unlearned(conn)
            if not items or not feedback.ready(items):
                return None
            try:
                with self._gate.work():
                    raw = self._provider.chat(feedback.build_messages(items), temperature=0.3, json_mode=True).content
                data = planner.load_json(raw)
            except ModelUnavailableError as exc:
                self._backoff_until = self._clock() + MODEL_BACKOFF
                log.info("Aprendizado aguardando o modelo: %s", exc)
                return None
            except (ModelError, planner.PlanError) as exc:
                log.info("Aprendizado com reações falhou: %s", exc)
                data = {}
            with transaction(conn):
                saved = feedback.apply_learning(conn, items, data, self._now())
                log_audit(conn, "lesson.from_feedback", {"feedback": [i["id"] for i in items], "lessons": saved})
            return {"learned_from_feedback": len(items), "lessons": saved}
        finally:
            conn.close()

    # --- vontades: objetivos que o Nym cria por conta própria (ver desires.py) ---------------------------------------------------------
    def _desire_step(self) -> dict[str, Any] | None:
        """Última prioridade do trabalhador: só roda quando não há nada mais a fazer (fila, planejamento, avaliação)."""
        cfg = self.get_autonomy()
        if not cfg["enabled"] or self._chat_is_recent():
            return None
        now = self._clock()
        if self._backoff_until and now < self._backoff_until:
            return None
        conn = self._conn()
        try:
            trigger = self._desire_trigger(conn, cfg, now)
            if trigger is None:
                return None
            return self._run_desires(conn, cfg, trigger, now)
        finally:
            conn.close()


    @staticmethod
    def _nym_open_goals(conn: sqlite3.Connection) -> int:
        return conn.execute("SELECT COUNT(*) FROM goals WHERE mode = 'task' AND origin = 'nym' AND status IN ('active', 'proposed')").fetchone()[0]


    def _desire_trigger(self, conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime) -> str | None:
        """Decide se é hora de o Nym pensar no que quer fazer. Devolve o gatilho ('dream', 'interval', 'free_time') ou None."""
        if self._nym_open_goals(conn) >= cfg["max_active"]:
            return None
        if cfg["max_per_day"] > 0:
            day_start = now.astimezone(self._tz).replace(hour=0, minute=0, second=0, microsecond=0)
            made = conn.execute("SELECT COUNT(*) FROM goals WHERE origin = 'nym' AND source IN ('desire', 'exploration') AND created_at >= ?",
                                (day_start.astimezone(timezone.utc).isoformat(timespec="milliseconds"),)).fetchone()[0]
            if made >= cfg["max_per_day"]:
                return None
        last = conn.execute("SELECT * FROM desire_runs ORDER BY id DESC LIMIT 1").fetchone()
        if last is not None and last["status"] == "failed" and now - _parse(last["started_at"]) < DESIRE_FAILURE_BACKOFF:
            return None
        since = last["started_at"] if last is not None else ""
        if conn.execute("SELECT 1 FROM dream_cycles WHERE status IN ('completed', 'partial') AND COALESCE(ended_at, started_at) > ? LIMIT 1",
                        (since,)).fetchone():
            return "dream"
        if last is not None and now - _parse(last["started_at"]) < timedelta(hours=cfg["interval_hours"]):
            return self._free_time(conn, cfg, now, last)
        new_memory = conn.execute("SELECT 1 FROM memories WHERE status = 'active' AND updated_at > ? LIMIT 1", (since,)).fetchone()
        open_question = conn.execute("SELECT 1 FROM dream_items WHERE kind = 'open_question' AND status = 'open' LIMIT 1").fetchone()
        pending_ex = conn.execute("SELECT 1 FROM exploration_tasks WHERE status = 'pending' LIMIT 1").fetchone()
        if new_memory or open_question or pending_ex:
            return "interval"
        return self._free_time(conn, cfg, now, last)

    def _free_time(self, conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime, last) -> str | None:
        """Fase 8.2: tempo livre. Sem nada novo e sem nada dele em andamento, ele não fica parado esperando você:
        de tempos em tempos decide algo para fazer (criar, assistir, continuar algo, pesquisar um gosto)."""
        minutes = cfg.get("free_time_minutes", 0)
        if not minutes or self._nym_open_goals(conn) > 0 or self._gate.idle_seconds() < FREE_TIME_IDLE.total_seconds():
            return None
        if last is not None and now - _parse(last["started_at"]) < self._free_time_wait(conn, minutes):
            return None
        return "free_time"

    @staticmethod
    def _free_time_wait(conn: sqlite3.Connection, minutes: int) -> timedelta:
        """Visto no uso real: a rodada repetia uma ideia, era descartada e ele ficava 90 min parado. Rodada que não deu
        em nada tenta de novo logo (15 min, depois 30, 60...) até o intervalo normal; rodada que deu certo espera o normal."""
        nothing = 0
        for r in conn.execute("SELECT status FROM desire_runs ORDER BY id DESC LIMIT 6"):
            if r["status"] != "nothing":
                break
            nothing += 1
        if not nothing:
            return timedelta(minutes=minutes)
        return timedelta(minutes=min(minutes, FREE_TIME_RETRY_MINUTES * 2 ** (nothing - 1)))


    def _run_desires(self, conn: sqlite3.Connection, cfg: dict[str, Any], trigger: str, now: datetime) -> dict[str, Any]:
        with transaction(conn):
            cur = conn.execute("INSERT INTO desire_runs (started_at, status, trigger) VALUES (?, 'running', ?)", (self._now(), trigger))
            run_id = int(cur.lastrowid)
        room = cfg["max_active"] - self._nym_open_goals(conn)
        approved: list[int] = []
        # 1) explorações que os sonhos registraram: ele mesmo decide investigar (dentro do espaço livre)
        if cfg["auto_start"]:
            with transaction(conn):
                for ex in conn.execute("SELECT id FROM exploration_tasks WHERE status = 'pending' ORDER BY id LIMIT ?", (max(0, room),)).fetchall():
                    conn.execute("UPDATE exploration_tasks SET status = 'approved', updated_at = ? WHERE id = ?", (self._now(), ex["id"]))
                    approved.append(ex["id"])
            room -= len(approved)
        created: list[int] = []
        dropped: list[str] = []
        seen: dict[str, Any] = {}
        status, error = "nothing", None
        if room > 0:
            ctx = desires.gather_context(conn, (now - timedelta(days=30)).isoformat(timespec="milliseconds"))
            ctx["notes"] = desires.notes_overview(Path(self._settings.data_dir) / "notes")
            ctx["free_time"] = trigger == "free_time"
            ctx["rejected"] = desires.recent_rejections(conn)   # ideias que ele insistiu e já existiam
            try:   # Fase 9: o que ele quis ontem (diário) e como está agora (estado calculado)
                ctx["intention"] = diary_mod.latest_intention(conn, diary_mod.today_local(now, self._tz))
                st = state_mod.compute_state(state_mod.gather_inputs(conn, now), now)
                ctx["mood"] = {"label": st.label, "energy": st.energy, "curiosity": st.curiosity}
            except sqlite3.Error:
                log.exception("Sem diário/estado para as vontades.")
            seen = desires.seen_ids(ctx)
            if desires.has_material(ctx):
                max_new = min(room, cfg["max_per_run"])
                try:
                    with self._gate.work():
                        raw = self._provider.chat(desires.build_messages(ctx, self.registry, max_new), temperature=0.7,
                                                  json_mode=True).content
                    result = desires.parse_desires(raw, ctx, max_new)
                except ModelUnavailableError as exc:
                    self._backoff_until = self._clock() + MODEL_BACKOFF
                    status, error = "failed", f"modelo indisponível: {exc}"
                except (ModelError, desires.PlanError) as exc:
                    status, error = "failed", str(exc)
                else:
                    dropped = result.dropped
                    with transaction(conn):
                        for gid in result.revive[:max(0, max_new)]:   # quis de novo algo que travou: retoma
                            g = self._goal(conn, gid)
                            self._set_goal(conn, gid, status="active", plan_state="needs_plan", plan_attempts=0,
                                           replans=0, model_calls=0, retry_round=g["retry_round"] + 1, retry_after=None,
                                           previous_attempt=g["previous_attempt"] or f"Da outra vez parou: {g['last_error'] or 'sem motivo'}")
                            self._event(conn, "goal.revived", gid, by="nym", reason="quis de novo numa vontade")
                            created.append(gid)
                        for d in result.desires[:max(0, max_new - len(result.revive))]:
                            created.append(self._create_own_goal(conn, d, cfg["auto_start"]))
                        for c in result.close_projects:
                            if projects_mod.close(conn, c["id"], c["status"], c["note"], self._now()):
                                self._event(conn, "project.closed", None, project_id=c["id"], status=c["status"], note=c["note"])
        if approved or created:
            status = "done"
        with transaction(conn):
            conn.execute("UPDATE desire_runs SET ended_at = ?, status = ?, seen = ?, created_goal_ids = ?, approved_exploration_ids = ?, "
                         "dropped = ?, error = ? WHERE id = ?",
                         (self._now(), status, desires.dumps(seen), desires.dumps(created), desires.dumps(approved),
                          desires.dumps(dropped), (error or "")[:500] or None, run_id))
            log_audit(conn, "desires.run", {"run_id": run_id, "status": status, "goals": created, "explorations": approved})
        return {"desire_run": run_id, "status": status, "created": created, "approved_explorations": approved}


    def _create_own_goal(self, conn: sqlite3.Connection, d: "desires.Desire", auto_start: bool) -> int:
        ts = self._now()
        project_id = d.project_id
        if project_id is None and d.new_project:
            project_id = projects_mod.create(conn, d.new_project["title"], d.new_project["why"], d.refs, ts)
            self._event(conn, "project.created", None, project_id=project_id, title=d.new_project["title"])
        cur = conn.execute(
            "INSERT INTO goals (description, origin, priority, status, created_at, updated_at, mode, completion_criteria, "
            "plan_state, source, motivation, refs) VALUES (?, 'nym', ?, ?, ?, ?, 'task', ?, ?, 'desire', ?, ?)",
            (d.description, d.priority, "active" if auto_start else "proposed", ts, ts, d.criteria,
             "needs_plan" if auto_start else "idle", d.why, desires.dumps(d.refs)))
        goal_id = int(cur.lastrowid)
        if project_id is not None:
            conn.execute("UPDATE goals SET project_id = ? WHERE id = ?", (project_id, goal_id))
            projects_mod.touch(conn, project_id, ts)
        self._event(conn, "goal.created", goal_id, origin="nym", source="desire", refs=d.refs)
        log_audit(conn, "goal.created", {"goal_id": goal_id, "origin": "nym", "source": "desire", "auto_start": auto_start})
        topic = (f"Decidi por conta própria: {d.description}. Motivo registrado: {d.why}" if auto_start else
                 f"Tive uma ideia e queria o ok dele: {d.description}. Motivo registrado: {d.why}. Ele aprova na página Tarefas.")
        if not auto_start:   # Fase 9: ideia que precisa do ok dele avisa; decisão própria fica para o diário/conversa
            self._notify(conn, "goal_created", topic, goal_id)
        return goal_id


class TaskWorker:
    """Thread que avança a fila em segundo plano. Uma ação por volta, para não monopolizar a GPU nem o banco."""

    def __init__(self, manager: TaskManager, interval: float) -> None:
        self._manager = manager
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                for _ in range(MAX_ACTIONS_PER_WAKE):          # continua enquanto houver o que fazer, com teto
                    if self._stop.is_set() or not self._manager.tick():
                        break
            except Exception:  # noqa: BLE001 - o trabalhador nunca deve morrer por um erro isolado
                log.exception("Erro no trabalhador de tarefas")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nym-task-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

