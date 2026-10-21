"""Fase 8.2: autonomia de verdade — trabalho em rodadas (sem chutar argumentos), recomeço sozinho quando trava,
tempo livre e o botão "confiar no risco médio". Casos tirados do uso real (planos com 'exemplo.com', 'Título da
nota encontrada' e objetivos que pausavam pedindo ajuda)."""
import json
from datetime import timedelta, timezone

from app.database.connection import connect, transaction
from app.memory.service import MemoryService
from app.tasks import planner
from app.tasks.manager import TaskManager
from app.tools.base import ToolContext
from app.tools.registry import ToolRegistry
from tests.dreamtools import add_mem, rows
from tests.fakes import DbTestCase, ScriptedProvider
from tests.memtools import BASE, clock_at
from tests.test_research import FakeHttp

SEARCH = {"results": [{"title": "Digimon — evolução", "url": "https://wiki.digimon.net/evolucao", "content": "Digievolução..."}]}


def plan(*steps):
    return json.dumps({"steps": [{"title": t, "tool": tool, "args": args} for t, tool, args in steps]})


def evaluation(done, summary, *steps):
    return json.dumps({"done": done, "summary": summary,
                       "steps": [{"title": t, "tool": tool, "args": args} for t, tool, args in steps]})


class Clock:
    def __init__(self, minutes=0):
        self.minutes = minutes

    def __call__(self):
        return BASE + timedelta(minutes=self.minutes)


class Gate:
    """Porteiro falso: o chat está parado há `idle` segundos."""
    def __init__(self, idle=3600.0):
        self.idle = idle

    def is_busy(self):
        return False

    def idle_seconds(self):
        return self.idle

    def work(self):
        from contextlib import nullcontext
        return nullcontext()


class GuessTests(DbTestCase):
    def test_guessed_arguments_are_dropped(self):
        reg = ToolRegistry()
        p = planner.parse_plan(plan(("buscar", "web_search", {"query": "evolução digimon"}),
                                    ("ler", "fetch_page", {"url": "https://exemplo.com/pagina-encontrada-na-busca"}),
                                    ("ler nota", "read_note", {"title": "Título da nota encontrada (se houver)"}),
                                    ("guardar", "save_finding", {"text": "algo", "url": "URL do resultado"})), reg, 5)
        self.assertEqual([s.tool for s in p.steps], ["web_search"])
        self.assertEqual(len(p.dropped), 3)
        self.assertTrue(all("decida depois" in d for d in p.dropped))

    def test_a_plan_made_only_of_guesses_is_asked_again_not_given_up(self):
        provider = ScriptedProvider([plan(("ler", "read_note", {"title": "Nota relevante encontrada na etapa anterior"})),
                                     plan(("listar", "list_notes", {})),
                                     json.dumps({"done": True, "summary": "Vi as notas.", "steps": []})])
        m = TaskManager(self.settings, provider, ToolRegistry(), clock=clock_at(0), tz=timezone.utc, gate=Gate(3600))
        g = m.create_goal("Ver minhas notas")
        for _ in range(10):
            if m.tick() is None:
                break
        self.assertEqual(m.get_goal(g["id"])["status"], "done")


class RoundsTests(DbTestCase):
    def test_works_in_rounds_using_real_results(self):
        provider = ScriptedProvider([
            plan(("buscar", "web_search", {"query": "evolução digimon"})),
            evaluation(False, "Achei uma página; falta ler.", ("ler", "fetch_page", {"url": "https://wiki.digimon.net/evolucao"})),
            evaluation(False, "Li; falta guardar.", ("guardar", "save_finding",
                                                    {"text": "Digimons evoluem por estágios", "url": "https://wiki.digimon.net/evolucao"})),
            json.dumps({"done": True, "summary": "Pesquisei, li e guardei.", "steps": []}),
        ])
        m = TaskManager(self.settings, provider, ToolRegistry(), MemoryService(self.settings), clock=clock_at(0),
                        tz=timezone.utc, gate=Gate(3600))
        m.http = FakeHttp({"https://wiki.digimon.net/": ("https://wiki.digimon.net/evolucao", "text/html",
                                                            "<title>Evolução</title><p>Digimons evoluem por estágios.</p>")},
                          posts={"https://api.tavily.com/search": SEARCH})
        for tool in ("web_search", "fetch_page"):
            m.set_permission(tool, "auto")
        g = m.create_goal("Pesquisar evolução Digimon e guardar o que achar")
        for _ in range(30):
            if m.tick() is None:
                break
        got = m.get_goal(g["id"])
        self.assertEqual(got["status"], "done", got["last_error"])
        self.assertEqual([t["tool"] for t in got["tasks"]], ["web_search", "fetch_page", "save_finding"])
        self.assertEqual(got["replans"], 2)


class SelfRetryTests(DbTestCase):
    def own_goal(self, m):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            gid = conn.execute(
                "INSERT INTO goals (description, origin, priority, status, created_at, updated_at, mode, plan_state, source, "
                "motivation, refs) VALUES ('Pesquisar designs de Digimon', 'nym', 3, 'active', ?, ?, 'task', 'needs_plan', "
                "'desire', 'gosto de Digimon', '{}')", (BASE.isoformat(), BASE.isoformat())).lastrowid
        conn.close()
        return gid

    def run_all(self, m, limit=20):
        for _ in range(limit):
            if m.tick() is None:
                break

    def test_his_stuck_goal_restarts_alone_with_what_went_wrong(self):
        clock = Clock(0)
        provider = ScriptedProvider([
            plan(("conta", "calculate", {"expression": "abc"})),
            json.dumps({"done": False, "summary": "A conta não deu.", "steps": []}),     # travou
        ])
        m = TaskManager(self.settings, provider, ToolRegistry(), clock=clock, tz=timezone.utc, gate=Gate(3600))
        m.update_autonomy(free_time_minutes=0)
        gid = self.own_goal(m)
        self.run_all(m)
        g = m.get_goal(gid)
        self.assertEqual(g["status"], "paused")
        self.assertIsNotNone(g["retry_after"])
        self.assertIn("calculate", g["previous_attempt"])
        self.assertEqual(rows(self.settings, "SELECT COUNT(*) AS n FROM pending_messages WHERE kind = 'task_failed'")[0]["n"], 0)
        # meia hora depois ele recomeça sozinho, e o planejamento vê o que deu errado
        clock.minutes = 31
        provider.replies = [plan(("conta", "calculate", {"expression": "2+2"})),
                            json.dumps({"done": True, "summary": "Agora deu.", "steps": []})]
        self.run_all(m)
        g = m.get_goal(gid)
        self.assertEqual((g["status"], g["retry_round"]), ("done", 1))
        replan = [c for c in provider.calls if "planejando" in c[0].content][-1][1].content
        self.assertIn("Você já tentou este objetivo antes", replan)
        self.assertIn("calculate", replan)

    def test_he_only_asks_for_help_after_the_retries_run_out(self):
        clock = Clock(0)
        provider = ScriptedProvider([])
        m = TaskManager(self.settings, provider, ToolRegistry(), clock=clock, tz=timezone.utc, gate=Gate(3600))
        m.update_autonomy(free_time_minutes=0, self_retries=1)
        gid = self.own_goal(m)
        for round_ in range(2):
            provider.replies = [plan(("conta", "calculate", {"expression": "abc"})),
                                json.dumps({"done": False, "summary": "Não deu.", "steps": []})]
            self.run_all(m)
            clock.minutes += 31
        g = m.get_goal(gid)
        self.assertEqual((g["status"], g["retry_after"], g["retry_round"]), ("paused", None, 1))
        self.assertEqual(rows(self.settings, "SELECT COUNT(*) AS n FROM pending_messages WHERE kind = 'task_failed'")[0]["n"], 1)

    def test_your_goals_still_report_right_away(self):
        provider = ScriptedProvider([plan(("conta", "calculate", {"expression": "abc"})),
                                     json.dumps({"done": False, "summary": "Não deu.", "steps": []})])
        m = TaskManager(self.settings, provider, ToolRegistry(), clock=clock_at(0), tz=timezone.utc, gate=Gate(3600))
        m.update_autonomy(free_time_minutes=0)
        g = m.create_goal("Fazer uma conta")
        self.run_all(m)
        self.assertIsNone(m.get_goal(g["id"])["retry_after"])
        self.assertEqual(rows(self.settings, "SELECT COUNT(*) AS n FROM pending_messages WHERE kind = 'task_failed'")[0]["n"], 1)


class FreeTimeTests(DbTestCase):
    def manager(self, idle, minutes=0, replies=None):
        add_mem(self.settings, "O usuário gosta de Digimon e de editar vídeos")
        self.provider = ScriptedProvider(replies or [json.dumps({"goals": []})])
        return TaskManager(self.settings, self.provider, ToolRegistry(), MemoryService(self.settings),
                           clock=clock_at(minutes), tz=timezone.utc, gate=Gate(idle))

    def test_with_nothing_new_he_still_decides_something_in_free_time(self):
        m = self.manager(idle=3600)
        conn = connect(self.settings.db_path)
        with transaction(conn):   # já pensou há pouco por causa da lembrança nova: o "interval" não dispara
            conn.execute("INSERT INTO desire_runs (started_at, status, trigger) VALUES (?, 'nothing', 'interval')",
                         (BASE.isoformat(timespec="milliseconds"),))
        conn.close()
        self.assertIsNone(m.tick())                               # 90 min ainda não passaram
        m2 = TaskManager(self.settings, self.provider, ToolRegistry(), MemoryService(self.settings),
                         clock=clock_at(7 * 60), tz=timezone.utc, gate=Gate(3600))
        m2.update_autonomy(interval_hours=24)
        r = m2.tick()
        newest = max(m2.desire_runs(), key=lambda r: r["id"])
        self.assertEqual(newest["trigger"], "free_time")
        self.assertIn("TEMPO LIVRE", self.provider.calls[-1][1].content)

    def test_not_while_you_are_chatting(self):
        m = self.manager(idle=60, minutes=7 * 60)
        conn = connect(self.settings.db_path)
        with transaction(conn):
            conn.execute("INSERT INTO desire_runs (started_at, status, trigger) VALUES (?, 'nothing', 'interval')",
                         (BASE.isoformat(timespec="milliseconds"),))
        conn.close()
        m.update_autonomy(interval_hours=24)
        self.assertIsNone(m.tick())


class SoftFailureTests(DbTestCase):
    def test_reading_a_missing_note_lists_the_real_ones(self):
        reg = ToolRegistry()
        ctx = ToolContext(settings=self.settings, clock=clock_at(0))
        write = reg.get("write_note")
        write.run(ctx, write.validate({"title": "Digimon", "content": "Agumon e Gabumon."}))
        read = reg.get("read_note")
        out = read.run(ctx, read.validate({"title": "Digimon [INACABADA]"}))
        self.assertIn("Ainda não existe", out)
        self.assertIn("digimon", out)

    def test_trust_medium_opens_medium_risk_only(self):
        m = TaskManager(self.settings, ScriptedProvider([]), ToolRegistry())
        tools = {t["name"]: t for t in m.trust_medium()}
        for name, t in tools.items():
            if t["risk"] == "medium":
                self.assertEqual(t["mode"], "auto", name)
            if t["risk"] == "high":
                self.assertNotEqual(t["mode"], "auto", name)
