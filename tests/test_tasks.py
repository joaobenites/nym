"""Fase 5: objetivos, decomposição, fila, permissões, limites e retomada após reinício."""
import json
import time
import unittest
from datetime import timedelta, timezone

from app.database.connection import connect, transaction
from app.memory.service import MemoryService
from app.models.base import ModelUnavailableError
from app.tasks import planner
from app.tasks.manager import TaskInputError, TaskManager
from app.tools.base import Param, Tool, ToolContext, ToolError
from app.tools.builtin import BUILTIN_TOOLS, note_filename
from app.tools.registry import PermissionInputError, ToolRegistry
from tests.dreamtools import add_mem, rows
from tests.fakes import prompt_of, DbTestCase, ScriptedProvider
from tests.memtools import clock_at


def plan(*steps, reason=None):
    data = {"steps": [{"title": t, "tool": tool, "args": args} for t, tool, args in steps]}
    if reason:
        data["reason"] = reason
    return json.dumps(data, ensure_ascii=False)


def evaluation(done=True, summary="Feito: calculei e conferi a hora.", steps=()):
    return json.dumps({"done": done, "summary": summary,
                       "steps": [{"title": t, "tool": tool, "args": args} for t, tool, args in steps]})


def run_until_idle(manager, limit=50):
    results = []
    for _ in range(limit):
        r = manager.tick()
        if r is None:
            break
        results.append(r)
    return results


class ToolTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from app.config import Settings
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = ToolContext(settings=Settings(data_dir=Path(self.tmp.name)), clock=clock_at(0), tz=timezone.utc)
        self.reg = ToolRegistry()

    def run_tool(self, name, **args):
        tool = self.reg.get(name)
        return tool.run(self.ctx, tool.validate(args))

    def test_calculate_is_safe(self):
        self.assertEqual(self.run_tool("calculate", expression="(12*3)/4 + 2^3"), "(12*3)/4 + 2^3 = 17")
        for bad in ('__import__("os").system("x")', "open('a')", "9**999", "1/0", "[1]*9"):
            with self.assertRaises(ToolError, msg=bad):
                self.run_tool("calculate", expression=bad)

    def test_notes_stay_inside_their_folder(self):
        self.assertEqual(note_filename("../../Windows/System32"), "windows-system32.md")
        out = self.run_tool("write_note", title="Ideias de vídeo", content="editar a abertura")
        self.assertIn("data/notes/ideias-de-video.md", out)
        with self.assertRaises(ToolError):                               # não sobrescreve sem append
            self.run_tool("write_note", title="Ideias de vídeo", content="outra")
        self.run_tool("write_note", title="Ideias de vídeo", content="trilha", append=True)
        self.assertIn("trilha", self.run_tool("read_note", title="Ideias de vídeo"))

    def test_arguments_are_validated(self):
        tool = self.reg.get("calculate")
        for bad in ({}, {"expression": 3}, {"expression": "1", "extra": 1}, {"expression": "x" * 500}):
            with self.assertRaises(ToolError):
                tool.validate(bad)


class PlannerTests(unittest.TestCase):
    def test_unknown_tools_and_bad_args_are_dropped(self):
        reg = ToolRegistry()
        p = planner.parse_plan(plan(("hora", "datetime_now", {}), ("apagar", "delete_everything", {}),
                                    ("conta", "calculate", {"expr": "1"})), reg, 5)
        self.assertEqual([s.tool for s in p.steps], ["datetime_now"])
        self.assertEqual(len(p.dropped), 2)

    def test_step_limit(self):
        p = planner.parse_plan(plan(*[("hora", "datetime_now", {})] * 9), ToolRegistry(), 3)
        self.assertEqual(len(p.steps), 3)

    def test_invalid_json(self):
        with self.assertRaises(planner.PlanError):
            planner.parse_plan("não sei", ToolRegistry(), 3)
        with self.assertRaises(planner.PlanError):
            planner.parse_evaluation(json.dumps({"summary": "x"}), ToolRegistry(), 3)


class ManagerTests(DbTestCase):
    def manager(self, replies, registry=None, minutes=0, **overrides):
        settings = self.make_settings(**overrides) if overrides else self.settings
        self.provider = ScriptedProvider(replies)
        return TaskManager(settings, self.provider, registry or ToolRegistry(), MemoryService(settings),
                           clock=clock_at(minutes), tz=timezone.utc)

    def test_goal_is_planned_executed_evaluated_and_reported(self):
        m = self.manager([plan(("que horas são", "datetime_now", {}), ("conta", "calculate", {"expression": "6*7"})),
                          evaluation(summary="A conta deu 42.")])
        goal = m.create_goal("Calcular 6*7 e ver a hora", "ter o resultado da conta")
        run_until_idle(m)
        g = m.get_goal(goal["id"])
        self.assertEqual((g["status"], g["plan_state"]), ("done", "finished"))
        self.assertEqual(g["result"], "A conta deu 42.")
        self.assertEqual([t["status"] for t in g["tasks"]], ["done", "done"])
        self.assertEqual(g["tasks"][1]["result"], "6*7 = 42")
        events = {e["event"] for e in g["events"]}
        self.assertTrue({"goal.created", "goal.planned", "task.started", "task.done", "goal.done"} <= events)
        notes = rows(self.settings, "SELECT kind, topic FROM pending_messages")
        self.assertEqual(notes[-1]["kind"], "task_done")
        self.assertIn("42", notes[-1]["topic"])
        self.assertTrue(all(k["json_mode"] for k in self.provider.kwargs))   # planejar e avaliar pedem JSON

    def test_permission_is_asked_and_respected(self):
        m = self.manager([plan(("anotar", "write_note", {"title": "lista", "content": "comprar café"})), evaluation()])
        goal = m.create_goal("Anotar a lista")
        run_until_idle(m)
        task = m.get_goal(goal["id"])["tasks"][0]
        self.assertEqual(task["status"], "awaiting_permission")
        self.assertFalse((self.settings.data_dir / "notes" / "lista.md").exists())     # nada foi escrito
        self.assertEqual(rows(self.settings, "SELECT kind FROM pending_messages")[-1]["kind"], "task_permission")
        m.approve_task(task["id"])
        run_until_idle(m)
        self.assertTrue((self.settings.data_dir / "notes" / "lista.md").exists())
        self.assertEqual(m.get_goal(goal["id"])["status"], "done")

    def test_denied_tool_is_blocked_and_goal_pauses(self):
        m = self.manager([plan(("conta", "calculate", {"expression": "1+1"})),
                          evaluation(done=False, summary="A ferramenta foi negada.")])
        conn = connect(self.settings.db_path)
        with transaction(conn):
            m.registry.set_mode(conn, "calculate", "deny")
        conn.close()
        goal = m.create_goal("Somar")
        run_until_idle(m)
        g = m.get_goal(goal["id"])
        self.assertEqual(g["tasks"][0]["status"], "blocked")
        self.assertEqual(g["status"], "paused")
        self.assertEqual(rows(self.settings, "SELECT kind FROM pending_messages")[-1]["kind"], "task_failed")

    def test_revoking_permission_counts_even_after_queueing(self):
        m = self.manager([plan(("conta", "calculate", {"expression": "1+1"}))])
        goal = m.create_goal("Somar")
        m.tick()                                                     # só planeja
        self.assertEqual(m.get_goal(goal["id"])["tasks"][0]["status"], "queued")
        m.set_permission("calculate", "ask")
        m.tick()
        self.assertEqual(m.get_goal(goal["id"])["tasks"][0]["status"], "awaiting_permission")

    def test_high_risk_tools_never_run_alone(self):
        danger = Tool("format_disk", "perigo", {}, "high", lambda ctx, a: "feito")
        reg = ToolRegistry([*BUILTIN_TOOLS, danger])
        conn = connect(self.settings.db_path)
        try:
            self.assertEqual(reg.mode(conn, "format_disk"), "deny")
            with self.assertRaises(PermissionInputError):
                with transaction(conn):
                    reg.set_mode(conn, "format_disk", "auto")
            conn.execute("INSERT INTO tool_permissions VALUES ('format_disk', 'auto', 'x')")  # mesmo forçando no banco
            self.assertEqual(reg.mode(conn, "format_disk"), "ask")
        finally:
            conn.close()

    def test_steps_run_in_order_and_a_failure_stops_the_rest(self):
        m = self.manager([plan(("conta errada", "calculate", {"expression": "abc"}), ("conta", "calculate", {"expression": "2+2"})),
                          evaluation(done=False, summary="A conta não era válida.")])
        goal = m.create_goal("Ler e somar")
        run_until_idle(m)
        tasks = m.get_goal(goal["id"])["tasks"]
        self.assertEqual([t["status"] for t in tasks], ["failed", "cancelled"])
        self.assertIsNone(tasks[1]["started_at"])                    # o segundo passo nunca rodou

    def test_retryable_errors_respect_the_attempt_limit(self):
        calls = []

        def flaky(ctx, args):
            calls.append(1)
            raise ToolError("serviço instável", retryable=True)
        reg = ToolRegistry([Tool("flaky", "instável", {}, "low", flaky)])
        m = self.manager([plan(("tentar", "flaky", {})), evaluation(done=False, summary="Não funcionou.")], reg,
                         task_max_attempts=3)
        goal = m.create_goal("Tentar")
        run_until_idle(m)
        self.assertEqual(len(calls), 3)
        self.assertEqual(m.get_goal(goal["id"])["tasks"][0]["status"], "failed")

    def test_tool_timeout(self):
        reg = ToolRegistry([Tool("slow", "lento", {}, "low", lambda c, a: time.sleep(3) or "ok")])
        m = self.manager([plan(("lento", "slow", {}))], reg, task_timeout_seconds=1, task_max_attempts=1)
        goal = m.create_goal("Esperar")
        m.tick()
        m.tick()
        task = m.get_goal(goal["id"])["tasks"][0]
        self.assertEqual(task["status"], "failed")
        self.assertIn("tempo limite", task["error"])

    def test_pending_task_resumes_after_restart(self):
        m = self.manager([plan(("conta", "calculate", {"expression": "3*3"}))])
        goal = m.create_goal("Multiplicar")
        m.tick()
        conn = connect(self.settings.db_path)
        with transaction(conn):        # simula o programa morrendo no meio da execução
            conn.execute("UPDATE tasks SET status = 'running', attempts = 1 WHERE goal_id = ?", (goal["id"],))
        conn.close()
        m2 = self.manager([evaluation(summary="Deu 9.")])           # "novo processo"
        self.assertEqual(m2.recover(), 1)
        run_until_idle(m2)
        g = m2.get_goal(goal["id"])
        self.assertEqual(g["status"], "done")
        self.assertEqual(g["tasks"][0]["result"], "3*3 = 9")
        self.assertIn("task.resumed_after_restart", {e["event"] for e in g["events"]})

    def test_invalid_plans_pause_after_the_limit(self):
        m = self.manager(["lixo", "{}", "nada"], goal_max_plan_attempts=3)
        goal = m.create_goal("Algo")
        run_until_idle(m)
        g = m.get_goal(goal["id"])
        self.assertEqual(g["status"], "paused")
        self.assertEqual(g["plan_attempts"], 3)
        self.assertEqual(len(self.provider.calls), 3)

    def test_empty_plan_explains_why(self):
        m = self.manager([plan(reason="preciso de internet para isso")])
        goal = m.create_goal("Ver a previsão do tempo")
        run_until_idle(m)
        g = m.get_goal(goal["id"])
        self.assertEqual(g["status"], "paused")
        self.assertIn("internet", g["last_error"])

    def test_replanning_is_limited(self):
        more = ("conta", "calculate", {"expression": "1+1"})
        m = self.manager([plan(more), evaluation(done=False, steps=[more]), evaluation(done=False, steps=[more]),
                          evaluation(done=False, steps=[more])], goal_max_replans=1)
        goal = m.create_goal("Algo que nunca termina")
        run_until_idle(m)
        g = m.get_goal(goal["id"])
        self.assertEqual(g["status"], "paused")
        self.assertEqual(g["replans"], 1)
        self.assertEqual(len(g["tasks"]), 2)

    def test_model_call_limit(self):
        more = ("conta", "calculate", {"expression": "1+1"})
        m = self.manager([plan(more)] + [evaluation(done=False, steps=[more])] * 10,
                         goal_max_model_calls=2, goal_max_replans=10)
        goal = m.create_goal("Algo")
        run_until_idle(m)
        g = m.get_goal(goal["id"])
        self.assertEqual(g["status"], "paused")
        self.assertEqual(len(self.provider.calls), 2)
        self.assertIn("limite de chamadas", g["last_error"])

    def test_model_unavailable_keeps_everything(self):
        m = self.manager([ModelUnavailableError("ollama fora")])
        goal = m.create_goal("Algo")
        self.assertEqual(m.tick()["status"], "waiting_model")
        g = m.get_goal(goal["id"])
        self.assertEqual((g["status"], g["plan_state"], g["model_calls"]), ("active", "needs_plan", 0))
        self.assertIsNone(m.tick())                                   # espera antes de tentar de novo

    def test_cancel_goal_cancels_open_tasks(self):
        m = self.manager([plan(("a", "calculate", {"expression": "1"}), ("b", "calculate", {"expression": "2"}))])
        goal = m.create_goal("Algo")
        m.tick()
        m.cancel_goal(goal["id"])
        self.assertEqual({t["status"] for t in m.get_goal(goal["id"])["tasks"]}, {"cancelled"})
        m.update_autonomy(free_time_minutes=0)                        # sem o "tempo livre" (Fase 8.2), não há nada a fazer
        self.assertIsNone(m.tick())

    def test_paused_worker_does_nothing(self):
        m = self.manager([plan(("a", "calculate", {"expression": "1"}))])
        m.create_goal("Algo")
        m.set_paused(True)
        self.assertIsNone(m.tick())
        self.assertEqual(self.provider.calls, [])

    def test_ongoing_goals_are_never_executed(self):
        from app.core.goals import GoalService
        GoalService(self.settings).add_goal("Conhecer melhor o usuário")   # objetivo da Fase 2 (só contexto)
        m = self.manager([plan(("a", "calculate", {"expression": "1"}))])
        m.update_autonomy(free_time_minutes=0)
        self.assertIsNone(m.tick())
        self.assertEqual(self.provider.calls, [])

    def test_deadline(self):
        m = self.manager([], minutes=120)
        goal = m.create_goal("Algo", deadline="2026-10-01T10:30:00+00:00")
        m.tick()
        g = m.get_goal(goal["id"])
        self.assertEqual(g["status"], "paused")
        self.assertIn("prazo", g["last_error"])

    def test_approved_exploration_becomes_a_goal_and_gets_the_result(self):
        a = add_mem(self.settings, "O usuário quer editar vídeos para o canal")
        conn = connect(self.settings.db_path)
        with transaction(conn):
            conn.execute("INSERT INTO exploration_tasks (question, refs, status, created_at, updated_at) "
                         "VALUES ('O que o usuário já disse sobre editar vídeos?', ?, 'approved', 'x', 'x')", (json.dumps([a]),))
        conn.close()
        m = self.manager([plan(("procurar", "memory_search", {"query": "editar vídeos canal"})),
                          evaluation(summary="Ele quer editar vídeos para o canal.")])
        run_until_idle(m)
        ex = rows(self.settings, "SELECT * FROM exploration_tasks")[0]
        self.assertEqual(ex["status"], "done")
        self.assertEqual(ex["result"], "Ele quer editar vídeos para o canal.")
        task = m.get_goal(ex["goal_id"])["tasks"][0]
        self.assertIn("editar vídeos", task["result"])

    def test_input_validation(self):
        m = self.manager([])
        for kwargs in ({"description": ""}, {"description": "x", "priority": 9}, {"description": "x", "deadline": "amanhã"}):
            with self.assertRaises(TaskInputError):
                m.create_goal(**kwargs)

    def test_chat_block_reports_only_real_records(self):
        m = self.manager([plan(("a", "calculate", {"expression": "2*2"})), evaluation(summary="Deu 4.")])
        self.assertEqual(m.block_for_chat(), "")
        m.create_goal("Fazer uma conta")
        run_until_idle(m)
        block = m.block_for_chat()
        self.assertIn("Fazer uma conta (concluído)", block)
        self.assertIn("Deu 4.", block)


if __name__ == "__main__":
    unittest.main()


class ChatKnowsItsTasksTests(DbTestCase):
    def test_chat_prompt_lists_real_tasks_and_the_rule(self):
        from app.core.chat_service import ChatService
        from tests.fakes import FakeProvider
        m = TaskManager(self.settings, ScriptedProvider([plan(("a", "calculate", {"expression": "5*5"})),
                                                         evaluation(summary="Deu 25.")]), ToolRegistry(),
                        clock=clock_at(0), tz=timezone.utc)
        m.create_goal("Calcular 5*5")
        run_until_idle(m)
        provider = FakeProvider()
        ChatService(self.settings, provider, tasks=m).send_message("e aí, fez a conta?")
        system = prompt_of(provider.calls[-1])
        self.assertIn("Calcular 5*5 (concluído). Resultado: Deu 25.", system)
        self.assertIn("só diga que fez algo se estiver lá", system)
