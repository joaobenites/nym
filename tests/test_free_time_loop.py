"""Uso real (18/10): "ele não faz nada sozinho". A ideia do diário já existia, cada rodada repetia a mesma ideia,
ela era descartada e ele esperava 90 min. Agora: reserva de ideias, ideias repetidas no prompt, retentativa rápida."""
import json
from datetime import timedelta, timezone

from app.database.connection import connect, transaction
from app.database.migrator import initialize_database
from app.memory.service import MemoryService
from app.tasks import desires
from app.tasks.manager import TaskManager
from app.tools.registry import ToolRegistry
from tests.dreamtools import add_mem, rows
from tests.fakes import DbTestCase, ScriptedProvider
from tests.memtools import BASE, clock_at
from tests.test_autonomy import Gate

REPEATED = "Investigar como a opinião de João sobre Digimon se relaciona com meus desenhos"


def run_row(settings, minutes_ago, status, dropped=()):
    conn = connect(settings.db_path)
    with transaction(conn):
        conn.execute("INSERT INTO desire_runs (started_at, status, trigger, dropped) VALUES (?, ?, 'free_time', ?)",
                     ((BASE - timedelta(minutes=minutes_ago)).isoformat(timespec="milliseconds"), status, json.dumps(list(dropped))))
    conn.close()


class FreeTimeLoopTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.mem = add_mem(self.settings, "O usuário gosta de Digimon e de desenhos humanoides")
        self.provider = ScriptedProvider([json.dumps({"goals": []})] * 5)

    def manager(self, minutes=0):
        m = TaskManager(self.settings, self.provider, ToolRegistry(), MemoryService(self.settings),
                        clock=clock_at(minutes), tz=timezone.utc, gate=Gate(3600))
        m.update_autonomy(interval_hours=24)
        return m

    def test_default_is_30_minutes(self):
        self.assertEqual(self.manager().get_autonomy()["free_time_minutes"], 30)

    def test_a_run_that_gave_nothing_tries_again_soon_then_backs_off(self):
        run_row(self.settings, 16, "nothing", [f"repetido: {REPEATED}"])
        r = self.manager().tick()                      # 16 min depois de uma rodada vazia: tenta de novo
        self.assertIsNotNone(r)
        self.assertIn(REPEATED[:40], self.provider.calls[-1][1].content)   # e vê a ideia que já existia
        # duas vazias seguidas: espera 30 min, não 15
        self.assertIsNone(self.manager(minutes=16).tick())
        self.assertIsNotNone(self.manager(minutes=31).tick())

    def test_after_a_run_that_worked_waits_the_normal_time(self):
        run_row(self.settings, 16, "done")
        self.assertIsNone(self.manager().tick())
        self.assertIsNotNone(self.manager(minutes=15).tick())

    def test_reserve_idea_is_used_when_the_first_repeats(self):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            conn.execute("INSERT INTO goals (description, origin, priority, status, created_at, updated_at, mode) "
                         "VALUES (?, 'nym', 3, 'done', ?, ?, 'task')", (REPEATED, BASE.isoformat(), BASE.isoformat()))
        ctx = desires.gather_context(conn, "")
        conn.close()
        idea = lambda d: {"description": d, "why": "ele curte", "refs": {"memories": [self.mem]}}  # noqa: E731
        raw = json.dumps({"goals": [idea(REPEATED), idea("Desenhar um Digimon humanoide com armadura de dados")]})
        r = desires.parse_desires(raw, ctx, 1)
        self.assertEqual([d.description for d in r.desires], ["Desenhar um Digimon humanoide com armadura de dados"])

    def test_diary_intention_that_already_exists_is_marked(self):
        ctx = {"identity": [], "cycles": [], "dream_items": [], "questions": [], "memories": [], "existing_goals": [REPEATED],
               "intention": {"id": 1, "day": "2026-10-17", "tomorrow": REPEATED}}
        text = desires.build_messages(ctx, ToolRegistry(), 2)[1].content
        self.assertIn("já virou objetivo", text)
        self.assertIn("Até 4 ideias", desires.build_messages(ctx, ToolRegistry(), 2)[0].content)


class MigrationTests(DbTestCase):
    def test_old_default_90_becomes_30_but_a_chosen_value_stays(self):
        for stored, expected in ((90, 30), (45, 45)):
            conn = connect(self.settings.db_path)
            with transaction(conn):
                conn.execute("INSERT OR REPLACE INTO app_state (key, value, updated_at) VALUES ('autonomy_settings', ?, ?)",
                             (json.dumps({"free_time_minutes": stored, "enabled": True}), BASE.isoformat()))
                conn.execute("DELETE FROM schema_migrations WHERE version = 18")
            conn.close()
            initialize_database(self.settings.db_path)
            value = json.loads(rows(self.settings, "SELECT value FROM app_state WHERE key = 'autonomy_settings'")[0]["value"])
            self.assertEqual((value["free_time_minutes"], value["enabled"]), (expected, True))
