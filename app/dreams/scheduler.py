"""Quando sonhar: manual, ao ficar ocioso, ou em horário agendado, sempre com limites e sem competir com o chat."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Callable

from app.config import Settings
from app.core.activity import ActivityGate
from app.database.connection import connect
from app.dreams.engine import DreamEngine
from app.dreams.journal import DreamJournal

log = logging.getLogger("nym.dreams")
FAILURE_BACKOFF = timedelta(minutes=30)


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class DreamScheduler:
    def __init__(self, engine: DreamEngine, journal: DreamJournal, gate: ActivityGate, settings: Settings,
                 clock: Callable[[], datetime] | None = None, tz: tzinfo | None = None, interval: float = 60.0) -> None:
        self._engine, self._journal, self._gate, self._settings = engine, journal, gate, settings
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._tz = tz
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def decide(self) -> tuple[str | None, str]:
        """Devolve (gatilho, motivo). Gatilho None = não sonhar agora; o motivo explica (aparece no painel)."""
        s = self._settings
        if not s.dreams_enabled:
            return None, "desativado na configuração"
        if self._journal.is_paused():
            return None, "pausado pelo usuário"
        if self._engine.is_running():
            return None, "já existe um ciclo em andamento"
        now = self._clock()
        conn = connect(s.db_path)
        try:
            if self._gate.is_busy():
                return None, "o chat está ocupado"
            last_msg = conn.execute("SELECT created_at FROM messages ORDER BY id DESC LIMIT 1").fetchone()
            idle = min(self._gate.idle_seconds(), (now - _parse(last_msg["created_at"])).total_seconds() if last_msg else float("inf"))
            if idle < s.dream_idle_minutes * 60:
                return None, f"aguardando {s.dream_idle_minutes} min sem conversa"
            if conn.execute("SELECT COUNT(*) FROM memory_jobs WHERE status = 'pending'").fetchone()[0]:
                return None, "há conversas ainda sendo consolidadas em memória"
            local = now.astimezone(self._tz)
            day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat(timespec="milliseconds")
            done_today = conn.execute("SELECT COUNT(*) FROM dream_cycles WHERE status IN ('completed', 'partial') AND started_at >= ?", (day_start,)).fetchone()[0]
            if done_today >= s.dream_max_per_day:
                return None, "limite diário de sonhos atingido"
            last_ok = conn.execute("SELECT started_at FROM dream_cycles WHERE status IN ('completed', 'partial') ORDER BY id DESC LIMIT 1").fetchone()
            if last_ok and now - _parse(last_ok["started_at"]) < timedelta(hours=s.dream_min_interval_hours):
                return None, "intervalo mínimo entre sonhos"
            bad = conn.execute("SELECT ended_at FROM dream_cycles WHERE status IN ('failed', 'interrupted') AND ended_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
            if bad and now - _parse(bad["ended_at"]) < FAILURE_BACKOFF:
                return None, "aguardando depois de uma falha recente"
            if s.dream_schedule_hour >= 0 and local.hour == s.dream_schedule_hour:
                ran = conn.execute("SELECT COUNT(*) FROM dream_cycles WHERE trigger = 'scheduled' AND started_at >= ?", (day_start,)).fetchone()[0]
                if not ran:
                    return "scheduled", f"horário agendado ({s.dream_schedule_hour:02d}h)"
            return "idle", f"{s.dream_idle_minutes} min sem conversa"
        finally:
            conn.close()

    def tick(self) -> dict[str, Any] | None:
        trigger, reason = self.decide()
        if trigger is None:
            return None
        return self._engine.run_cycle(trigger, reason)

    def status(self) -> dict[str, Any]:
        trigger, reason = self.decide()
        s = self._settings
        return {"enabled": s.dreams_enabled, "paused": self._journal.is_paused(), "running": self._engine.is_running(),
                "would_dream_now": trigger is not None, "reason": reason,
                "limits": {"idle_minutes": s.dream_idle_minutes, "min_interval_hours": s.dream_min_interval_hours,
                           "max_per_day": s.dream_max_per_day, "max_seconds": s.dream_max_seconds,
                           "max_model_calls": s.dream_max_model_calls, "max_memories": s.dream_max_memories,
                           "schedule_hour": s.dream_schedule_hour}}

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("Erro no agendador de sonhos")

    def start(self) -> None:
        self._journal.recover_stale()
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nym-dream-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
