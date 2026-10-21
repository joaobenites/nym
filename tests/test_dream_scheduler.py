import time
import unittest
from datetime import datetime, timedelta, timezone

from app.core.activity import ActivityGate
from app.core.identity import IdentityService
from app.database.connection import connect, transaction
from app.dreams.engine import DreamEngine
from app.dreams.journal import DreamJournal
from app.dreams.scheduler import DreamScheduler
from tests.dreamtools import add_mem, dream_reply, rows
from tests.fakes import DbTestCase, ScriptedProvider
from tests.memtools import at, clock_at, seed_chat


class SchedulerTests(DbTestCase):
    def build(self, minutes=40, replies=(), **overrides):
        settings = self.make_settings(**overrides)
        engine = DreamEngine(settings, ScriptedProvider(replies), IdentityService(settings), clock=clock_at(minutes))
        journal = DreamJournal(settings)
        gate = ActivityGate()
        return DreamScheduler(engine, journal, gate, settings, clock=clock_at(minutes), tz=timezone.utc, interval=0.05), engine, journal, gate

    def setUp(self):
        super().setUp()
        seed_chat(self.settings, [("user", "mensagem antiga o bastante para a conversa", 0)])  # última fala: minuto 0

    def cycle_row(self, status, started, ended=None, trigger="idle"):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            conn.execute("INSERT INTO dream_cycles (trigger, status, started_at, ended_at) VALUES (?, ?, ?, ?)", (trigger, status, at(started), at(ended) if ended is not None else None))
        conn.close()

    def test_idle_after_enough_silence(self):
        sched, *_ = self.build(minutes=40)
        self.assertEqual(sched.decide()[0], "idle")

    def test_waits_for_silence(self):
        sched, *_ = self.build(minutes=10)
        trigger, reason = sched.decide()
        self.assertIsNone(trigger)
        self.assertIn("sem conversa", reason)

    def test_disabled_paused_running_and_busy(self):
        sched, *_ = self.build(dreams_enabled=False)
        self.assertEqual(sched.decide()[1], "desativado na configuração")
        sched, engine, journal, gate = self.build()
        journal.set_paused(True)
        self.assertEqual(sched.decide()[1], "pausado pelo usuário")
        journal.set_paused(False)
        with gate.chat():
            self.assertEqual(sched.decide()[1], "o chat está ocupado")
        self.assertTrue(engine._lock.acquire(blocking=False))
        try:
            self.assertIn("em andamento", sched.decide()[1])
        finally:
            engine._lock.release()

    def test_does_not_dream_while_conversations_are_still_being_consolidated(self):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            conn.execute("INSERT INTO memory_jobs (start_message_id, end_message_id, message_count, status, created_at, updated_at) VALUES (1, 1, 1, 'pending', 'x', 'x')")
        conn.close()
        sched, *_ = self.build()
        self.assertIn("consolidadas", sched.decide()[1])

    def test_daily_limit_and_minimum_interval(self):
        for s in (5, 6, 7):
            self.cycle_row("completed", s)
        sched, *_ = self.build(minutes=60, dream_idle_minutes=1)
        self.assertEqual(sched.decide()[1], "limite diário de sonhos atingido")

    def test_minimum_interval_between_cycles(self):
        self.cycle_row("completed", 0)
        sched, *_ = self.build(minutes=120, dream_idle_minutes=1)
        self.assertEqual(sched.decide()[1], "intervalo mínimo entre sonhos")
        sched, *_ = self.build(minutes=60 * 7, dream_idle_minutes=1)
        self.assertEqual(sched.decide()[0], "idle")

    def test_backs_off_after_a_failure(self):
        self.cycle_row("failed", 90, ended=100)
        sched, *_ = self.build(minutes=110, dream_idle_minutes=1)
        self.assertIn("falha recente", sched.decide()[1])
        sched, *_ = self.build(minutes=140, dream_idle_minutes=1)
        self.assertEqual(sched.decide()[0], "idle")

    def test_scheduled_hour_runs_once_a_day(self):
        sched, *_ = self.build(minutes=5, dream_idle_minutes=1, dream_schedule_hour=10)   # BASE = 10:00 UTC
        self.assertEqual(sched.decide()[0], "scheduled")
        self.cycle_row("completed", 1, ended=2, trigger="scheduled")
        sched, *_ = self.build(minutes=500, dream_idle_minutes=1, dream_schedule_hour=10, dream_min_interval_hours=0)
        self.assertNotEqual(sched.decide()[0], "scheduled")

    def test_tick_runs_a_cycle_and_then_respects_the_interval(self):
        mid = add_mem(self.settings, "O usuário gosta de ler romances", importance=0.7)
        sched, *_ = self.build(replies=[dream_reply(review="Revisão de teste.", questions=[{"text": "Qual autor?", "evidence": [mid]}])])
        result = sched.tick()
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(sched.tick())

    def test_status_explains_itself(self):
        sched, *_ = self.build(minutes=10)
        st = sched.status()
        self.assertFalse(st["would_dream_now"])
        self.assertIn("reason", st)
        self.assertEqual(st["limits"]["max_per_day"], 3)

    def test_background_thread_recovers_stale_cycles_and_stops_cleanly(self):
        self.cycle_row("running", 0)
        mid = add_mem(self.settings, "O usuário gosta de ler romances", importance=0.7)
        later = lambda: datetime.now(timezone.utc) + timedelta(hours=2)   # relógio coerente com a hora real do ciclo recuperado
        settings = self.settings
        engine = DreamEngine(settings, ScriptedProvider([dream_reply()]), IdentityService(settings), clock=later)
        sched = DreamScheduler(engine, DreamJournal(settings), ActivityGate(), settings, clock=later, tz=timezone.utc, interval=0.05)
        sched.start()
        deadline = time.time() + 5
        while time.time() < deadline and not any(r["status"] == "completed" for r in rows(self.settings, "SELECT status FROM dream_cycles")):
            time.sleep(0.05)
        sched.stop()
        statuses = sorted(r["status"] for r in rows(self.settings, "SELECT status FROM dream_cycles"))
        self.assertEqual(statuses, ["completed", "interrupted"])
        self.assertFalse(sched._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
