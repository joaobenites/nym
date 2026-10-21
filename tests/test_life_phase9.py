"""Fase 9 — vida contínua (dia, diário, projetos, assuntos guardados) e as travas achadas no uso real."""
import json
from datetime import date, timedelta, timezone

from app.core.chat_service import ChatService
from app.database.connection import connect, transaction
from app.life import day as day_mod
from app.life import diary as diary_mod
from app.memory.service import MemoryService
from app.tasks import planner
from app.tasks.manager import TaskManager
from app.tools.registry import ToolRegistry
from tests.dreamtools import add_mem, rows
from tests.fakes import DbTestCase, ScriptedProvider
from tests.memtools import BASE, at, clock_at, seed_chat
from tests.test_autonomy import Clock, Gate

UTC = timezone.utc


def plan(*steps):
    return json.dumps({"steps": [{"title": t, "tool": tool, "args": args} for t, tool, args in steps]})


def sql(settings, query, *args):
    conn = connect(settings.db_path)
    try:
        with transaction(conn):
            return conn.execute(query, args).lastrowid
    finally:
        conn.close()


def own_goal(settings, desc="Pesquisar designs de Digimon", status="active", plan_state="needs_plan", **cols):
    gid = sql(settings, "INSERT INTO goals (description, origin, priority, status, created_at, updated_at, mode, plan_state, "
              "source, motivation, refs) VALUES (?, 'nym', 3, ?, ?, ?, 'task', ?, 'desire', 'gosto', '{}')",
              desc, status, at(0), at(0), plan_state)
    for k, v in cols.items():
        sql(settings, f"UPDATE goals SET {k} = ? WHERE id = ?", v, gid)  # noqa: S608 - só nomes fixos do teste
    return gid


def run_all(m, limit=30):
    out = []
    for _ in range(limit):
        r = m.tick()
        if r is None:
            break
        out.append(r)
    return out


class RealBugTests(DbTestCase):
    def test_an_old_failure_no_longer_cancels_the_new_round(self):
        gid = own_goal(self.settings)
        sql(self.settings, "INSERT INTO tasks (goal_id, step, title, tool, args, risk, origin, status, created_at, updated_at) "
                           "VALUES (?, 1, 'velho', 'read_note', '{}', 'low', 'nym', 'failed', ?, ?)", gid, at(0), at(0))
        provider = ScriptedProvider([plan(("conta", "calculate", {"expression": "2+2"})),
                                     json.dumps({"done": True, "summary": "Deu 4.", "steps": []})])
        m = TaskManager(self.settings, provider, ToolRegistry(), clock=clock_at(0), tz=UTC, gate=Gate())
        m.update_autonomy(free_time_minutes=0)
        run_all(m)
        g = m.get_goal(gid)
        self.assertEqual(g["status"], "done")
        self.assertEqual([t["status"] for t in g["tasks"]], ["failed", "done"])   # o passo novo rodou de verdade

    def test_steps_written_with_other_names_are_understood(self):
        ev = planner.parse_evaluation(json.dumps({"done": False, "summary": "x", "steps": [
            {"ferramenta": "web_search", "arguments": {"query": "digimon"}},
            {"tool": "fetch_page", "url": "https://pt.wikipedia.org/wiki/Digimon"}]}), ToolRegistry(), 5)
        self.assertEqual([(s.tool, s.args) for s in ev.plan.steps],
                         [("web_search", {"query": "digimon"}), ("fetch_page", {"url": "https://pt.wikipedia.org/wiki/Digimon"})])
        self.assertIn('"tool": "nome_da_ferramenta"', planner.eval_messages({"description": "x"}, [], ToolRegistry(), 3)[0].content)

    def test_active_but_finished_goal_is_unstuck(self):
        gid = own_goal(self.settings, status="active", plan_state="finished")
        provider = ScriptedProvider([plan(("conta", "calculate", {"expression": "1+1"})),
                                     json.dumps({"done": True, "summary": "ok", "steps": []})])
        m = TaskManager(self.settings, provider, ToolRegistry(), clock=clock_at(0), tz=UTC, gate=Gate())
        m.update_autonomy(free_time_minutes=0)
        run_all(m)
        self.assertEqual(m.get_goal(gid)["status"], "done")

    def test_wanting_a_stuck_goal_again_revives_it_instead_of_dropping(self):
        add_mem(self.settings, "O usuário curte Digimon")
        gid = own_goal(self.settings, status="paused", plan_state="finished", last_error="não achei")
        sql(self.settings, "INSERT INTO dream_cycles (trigger, status, started_at, ended_at, review) VALUES "
                           "('idle', 'completed', ?, ?, 'Pensei em Digimon.')", at(10), at(10))
        idea = {"description": "Pesquisar designs de Digimon marcantes", "why": "gosto", "priority": 3,
                "refs": {"cycles": [1]}}
        m = TaskManager(self.settings, ScriptedProvider([json.dumps({"goals": [idea]})]), ToolRegistry(),
                        MemoryService(self.settings), clock=clock_at(60), tz=UTC, gate=Gate())
        m.tick()
        g = m.get_goal(gid)
        self.assertEqual((g["status"], g["plan_state"], g["retry_round"]), ("active", "needs_plan", 1))
        self.assertIn("não achei", g["previous_attempt"])
        self.assertEqual(rows(self.settings, "SELECT COUNT(*) AS n FROM goals")[0]["n"], 1)   # não duplicou


class DayTests(DbTestCase):
    def test_day_is_made_of_records_only(self):
        seed_chat(self.settings, [("user", "oi", 0), ("assistant", "oi!", 1)])
        gid = own_goal(self.settings, desc="Desenhar uma criatura de dados", status="done", plan_state="finished")
        sql(self.settings, "UPDATE goals SET result = 'Desenhei.', updated_at = ? WHERE id = ?", at(30), gid)
        sql(self.settings, "INSERT INTO creations (kind, title, content, created_at) VALUES ('drawing', 'Criatura', '<svg/>', ?)", at(31))
        sql(self.settings, "INSERT INTO perceptions (kind, source, title, description, created_at) VALUES "
                           "('video', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'Como desenhar', 'resumo', ?)", at(40))
        start, end = day_mod.bounds(BASE.date(), UTC)
        conn = connect(self.settings.db_path)
        self.addCleanup(conn.close)   # Windows: conexão aberta trava o arquivo e a limpeza do teste falha
        text = day_mod.render(day_mod.facts(conn, start, end))
        self.assertIn("Terminou: Desenhar uma criatura de dados", text)
        self.assertIn('Criou: desenho "Criatura"', text)
        self.assertIn('Assistiu: "Como desenhar"', text)
        self.assertIn("Conversou com ele: 1 mensagem", text)
        away = day_mod.since(conn, at(0), at(60))
        self.assertNotIn("Conversou", away)                     # "enquanto você estava fora" é só o que ELE fez
        self.assertTrue(day_mod.is_empty(day_mod.facts(conn, *day_mod.bounds(BASE.date() - timedelta(days=5), UTC))))


class DiaryTests(DbTestCase):
    def setUp(self):
        super().setUp()
        sql(self.settings, "INSERT INTO creations (kind, title, content, created_at) VALUES ('drawing', 'Criatura', '<svg/>', ?)", at(0))

    def manager(self, replies, minutes=60 * 24 + 60):
        self.provider = ScriptedProvider(replies)
        m = TaskManager(self.settings, self.provider, ToolRegistry(), clock=clock_at(minutes), tz=UTC, gate=Gate())
        m.update_autonomy(free_time_minutes=0, enabled=False)
        return m

    def test_writes_yesterday_from_real_records_with_an_intention(self):
        m = self.manager(["Hoje desenhei uma criatura de dados e fiquei feliz com as cores, mesmo sem saber se ele vai curtir.\n"
                          "Amanhã: tentar uma segunda versão com olhos de código binário."])
        r = m.tick()
        self.assertEqual(r["diary"], BASE.date().isoformat())
        self.assertIn('Criou: desenho "Criatura"', self.provider.calls[0][1].content)
        page = rows(self.settings, "SELECT * FROM diary")[0]
        self.assertEqual(page["tomorrow"], "tentar uma segunda versão com olhos de código binário.")
        self.assertNotIn("Amanhã", page["text"])
        mem = rows(self.settings, "SELECT kind, content FROM memories WHERE id = ?", (page["memory_id"],))[0]
        self.assertTrue(mem["content"].startswith("Meu dia"))
        self.assertIsNone(m.tick())                              # não reescreve; dias vazios não ganham página
        conn = connect(self.settings.db_path)
        self.addCleanup(conn.close)
        self.assertEqual(diary_mod.latest_intention(conn, BASE.date() + timedelta(days=1))["tomorrow"], page["tomorrow"])

    def test_bad_page_is_not_saved_and_waits_to_retry(self):
        m = self.manager(["curto"])
        self.assertIsNone(m.tick())
        self.assertEqual(rows(self.settings, "SELECT COUNT(*) AS n FROM diary")[0]["n"], 0)
        self.assertIsNone(m.tick())                              # espera antes de tentar de novo
        self.assertEqual(len(self.provider.calls), 1)

    def test_today_is_not_written_yet(self):
        m = self.manager(["x" * 100], minutes=60)
        self.assertIsNone(m.tick())
        self.assertEqual(self.provider.calls, [])


class ProjectTests(DbTestCase):
    def manager(self, reply, minutes=60):
        self.provider = ScriptedProvider([reply])
        return TaskManager(self.settings, self.provider, ToolRegistry(), MemoryService(self.settings),
                           clock=clock_at(minutes), tz=UTC, gate=Gate())

    def test_opens_continues_and_closes_projects(self):
        mem = add_mem(self.settings, "O usuário curte criaturas digitais")
        sql(self.settings, "INSERT INTO dream_cycles (trigger, status, started_at, ended_at, review) VALUES "
                           "('idle', 'completed', ?, ?, 'Sonhei com criaturas.')", at(10), at(10))
        idea = {"description": "Desenhar a primeira criatura da minha coleção", "why": "quero uma coleção", "priority": 3,
                "refs": {"memories": [mem]}, "new_project": {"title": "Coleção de criaturas de dados", "why": "vale vários dias"}}
        m = self.manager(json.dumps({"goals": [idea]}))
        m.tick()
        proj = rows(self.settings, "SELECT * FROM projects")[0]
        goal = rows(self.settings, "SELECT * FROM goals")[0]
        self.assertEqual((proj["title"], goal["project_id"]), ("Coleção de criaturas de dados", proj["id"]))
        sql(self.settings, "UPDATE goals SET status = 'done', plan_state = 'finished', result = 'Desenhei a primeira.'")
        sql(self.settings, "INSERT INTO dream_cycles (trigger, status, started_at, ended_at, review) VALUES "
                           "('idle', 'completed', ?, ?, 'De novo criaturas.')", at(100), at(100))
        nxt = {"description": "Pesquisar como dar olhos de código às criaturas", "why": "próximo passo", "priority": 3,
               "refs": {}, "project": proj["id"]}
        m2 = self.manager(json.dumps({"goals": [nxt], "close_projects": []}), minutes=120)
        m2.tick()
        prompt = self.provider.calls[0][1].content
        self.assertIn("Coleção de criaturas de dados", prompt)
        self.assertIn("Desenhar a primeira criatura da minha coleção (feito)", prompt)
        second = rows(self.settings, "SELECT project_id FROM goals ORDER BY id DESC")[0]
        self.assertEqual(second["project_id"], proj["id"])       # continuar o projeto basta como registro real
        sql(self.settings, "UPDATE goals SET status = 'done', plan_state = 'finished'")
        sql(self.settings, "INSERT INTO dream_cycles (trigger, status, started_at, ended_at, review) VALUES "
                           "('idle', 'completed', ?, ?, 'Acho que terminei.')", at(200), at(200))
        m3 = self.manager(json.dumps({"goals": [], "close_projects": [{"id": proj["id"], "status": "done", "note": "coleção pronta"}]}),
                          minutes=220)
        m3.tick()
        self.assertEqual(rows(self.settings, "SELECT status, closing_note FROM projects")[0]["status"], "done")


class KeptForLaterTests(DbTestCase):
    def test_his_own_finished_things_wait_for_the_conversation(self):
        seed_chat(self.settings, [("user", "vou dormir", 0), ("assistant", "boa noite!", 1)])
        gid = own_goal(self.settings, desc="Desenhar uma criatura de dados")
        provider = ScriptedProvider([plan(("conta", "calculate", {"expression": "1+1"})),
                                     json.dumps({"done": True, "summary": "Desenhei a criatura.", "steps": []})])
        m = TaskManager(self.settings, provider, ToolRegistry(), clock=clock_at(60), tz=UTC, gate=Gate())
        m.update_autonomy(free_time_minutes=0)
        run_all(m)
        self.assertEqual(rows(self.settings, "SELECT COUNT(*) AS n FROM pending_messages")[0]["n"], 0)
        chat_provider = ScriptedProvider(["Bom dia! Enquanto você dormia eu desenhei uma criatura."])
        ChatService(self.settings, chat_provider, clock=clock_at(60 * 9), tz=UTC).send_message("bom dia")
        ctx = chat_provider.calls[0][-1].content
        self.assertIn("Enquanto ele esteve fora", ctx)
        self.assertIn("Terminou: Desenhar uma criatura de dados", ctx)

    def test_things_promised_in_chat_still_report(self):
        msg = seed_chat(self.settings, [("user", "pesquisa isso pra mim", 0), ("assistant", "deixa comigo", 1)])[1]
        gid = own_goal(self.settings, desc="Pesquisar o que ele pediu", chat_message_id=msg)
        provider = ScriptedProvider([plan(("conta", "calculate", {"expression": "1+1"})),
                                     json.dumps({"done": True, "summary": "Pronto.", "steps": []})])
        m = TaskManager(self.settings, provider, ToolRegistry(), clock=clock_at(60), tz=UTC, gate=Gate())
        m.update_autonomy(free_time_minutes=0)
        run_all(m)
        self.assertEqual(rows(self.settings, "SELECT kind FROM pending_messages")[0]["kind"], "task_done")


class DiaryInlineTomorrowTests(DbTestCase):
    def test_tomorrow_at_the_end_of_the_paragraph_is_still_found(self):
        # teste de cérebro real (qwen3.5:9b, 19/10): "Amanhã:" grudado no fim do parágrafo e a intenção se perdia
        text, tomorrow = diary_mod.parse("Hoje conversei muito com ele e atualizei minhas notas sobre Digimon. "
                                         "Amanhã: vou decidir qual Digimon desenhar.")
        self.assertEqual(tomorrow, "vou decidir qual Digimon desenhar.")
        self.assertNotIn("Amanhã", text)
        _, none = diary_mod.parse("Pensei no que farei amanhã: talvez desenhar. Foi um dia bom com muitas conversas.")
        self.assertIsNone(none)
