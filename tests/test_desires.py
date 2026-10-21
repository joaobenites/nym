"""Fase 5.1: o Nym cria objetivos próprios a partir de sonhos, perguntas, interesses e lembranças."""
import json
from datetime import timezone

from app.database.connection import connect, transaction
from app.memory.service import MemoryService
from app.models.base import ModelUnavailableError
from app.tasks import desires
from app.tasks.manager import TaskInputError, TaskManager
from app.tools.registry import ToolRegistry
from tests.dreamtools import add_mem, rows
from tests.fakes import DbTestCase, ScriptedProvider
from tests.memtools import at, clock_at


def idea(description, why="Sonhei com o canal dele e fiquei curioso.", memories=(), items=(), cycles=(), criteria=None):
    return {"description": description, "why": why, "criteria": criteria, "priority": 3,
            "refs": {"memories": list(memories), "dream_items": list(items), "cycles": list(cycles)}}


def ideas(*entries):
    return json.dumps({"goals": list(entries)}, ensure_ascii=False)


def plan(*steps):
    return json.dumps({"steps": [{"title": t, "tool": tool, "args": args} for t, tool, args in steps]})


def evaluation(summary="Feito."):
    return json.dumps({"done": True, "summary": summary, "steps": []})


class DesireBase(DbTestCase):
    def setUp(self):
        super().setUp()
        self.mem = add_mem(self.settings, "O usuário quer começar a editar vídeos para o canal")

    def dream(self, minutes=0, question=None):
        conn = connect(self.settings.db_path)
        try:
            with transaction(conn):
                cid = conn.execute("INSERT INTO dream_cycles (trigger, status, started_at, ended_at, review) "
                                   "VALUES ('idle', 'completed', ?, ?, 'Liguei o canal dele à vontade de editar vídeos.')",
                                   (at(minutes), at(minutes))).lastrowid
                item = None
                if question:
                    item = conn.execute("INSERT INTO dream_items (cycle_id, kind, content, refs, status, created_at) "
                                        "VALUES (?, 'open_question', ?, '[]', 'open', ?)", (cid, question, at(minutes))).lastrowid
            return cid, item
        finally:
            conn.close()

    def manager(self, replies, minutes=60, **autonomy):
        self.provider = ScriptedProvider(replies)
        m = TaskManager(self.settings, self.provider, ToolRegistry(), MemoryService(self.settings),
                        clock=clock_at(minutes), tz=timezone.utc)
        if autonomy:
            m.update_autonomy(**autonomy)
        return m

    def run_all(self, m, limit=40):
        out = []
        for _ in range(limit):
            r = m.tick()
            if r is None:
                break
            out.append(r)
        return out


class DesireTests(DesireBase):
    def test_after_a_dream_he_creates_and_runs_his_own_goal(self):
        cid, _ = self.dream(0)
        m = self.manager([
            ideas(idea("Anotar numa nota as ideias de vídeo que ele já me contou", memories=[self.mem], cycles=[cid],
                       criteria="nota criada")),
            plan(("procurar", "memory_search", {"query": "vídeos canal"})),
            evaluation("Juntei o que ele disse sobre o canal."),
        ])
        self.run_all(m)
        goal = rows(self.settings, "SELECT * FROM goals")[0]
        self.assertEqual((goal["origin"], goal["source"], goal["status"]), ("nym", "desire", "done"))
        self.assertIn("Sonhei com o canal", goal["motivation"])
        self.assertEqual(json.loads(goal["refs"])["memories"], [self.mem])
        run = m.desire_runs()[0]
        self.assertEqual((run["trigger"], run["status"], run["created_goal_ids"]), ("dream", "done", [goal["id"]]))
        kinds = [r["kind"] for r in rows(self.settings, "SELECT kind FROM pending_messages ORDER BY id")]
        self.assertEqual(kinds, [])        # Fase 9: decisão e conclusão só dele viram assunto guardado, não aviso
        events = [r["event"] for r in rows(self.settings, "SELECT event FROM task_events")]
        self.assertIn("goal.done_kept_for_later", events)
        self.assertIn("momento livre seu", self.provider.calls[0][0].content)            # 1ª chamada: decidir o que quer
        self.assertIn(f"[{self.mem}] O usuário quer começar a editar vídeos", self.provider.calls[0][1].content)

    def test_code_drops_ungrounded_repeated_and_sensitive_ideas(self):
        cid, item = self.dream(0, question="Que tipo de vídeo ele quer fazer?")
        conn = connect(self.settings.db_path)
        with transaction(conn):
            conn.execute("INSERT INTO goals (description, origin, priority, status, created_at, updated_at, mode) "
                         "VALUES ('Organizar as ideias de vídeo do canal', 'user', 3, 'active', ?, ?, 'ongoing')", (at(0), at(0)))
        conn.close()
        m = self.manager([ideas(
            idea("Aprender a tocar violão sozinho", memories=[999]),                                  # referência inventada
            idea("Organizar as ideias de vídeo do canal", memories=[self.mem]),                        # repetido
            idea("Guardar a senha do email dele numa nota", memories=[self.mem]),                     # sensível
            idea("Pensar em que tipo de vídeo ele quer fazer", items=[item]),                         # válido
            idea("Calcular quantos vídeos por mês ele consegue", memories=[self.mem]),                # além do limite
        )], max_per_run=1)
        m.tick()
        goals = rows(self.settings, "SELECT description FROM goals WHERE origin = 'nym'")
        self.assertEqual([g["description"] for g in goals], ["Pensar em que tipo de vídeo ele quer fazer"])
        dropped = m.desire_runs()[0]["dropped"]
        self.assertEqual(len(dropped), 4)
        self.assertTrue(any("sem registro real" in d for d in dropped))
        self.assertTrue(any("repetido" in d for d in dropped))
        self.assertTrue(any("sensível" in d for d in dropped))
        self.assertTrue(any("reserva não usada" in d for d in dropped))

    def test_without_auto_start_ideas_wait_for_approval(self):
        cid, _ = self.dream(0)
        m = self.manager([ideas(idea("Fazer as contas de quanto tempo leva editar um vídeo", cycles=[cid])),
                          plan(("conta", "calculate", {"expression": "3*20"})), evaluation()], auto_start=False)
        self.run_all(m)
        goal = rows(self.settings, "SELECT * FROM goals")[0]
        self.assertEqual((goal["status"], goal["plan_state"]), ("proposed", "idle"))
        self.assertEqual(len(self.provider.calls), 1)            # nada foi planejado sem o ok
        self.assertIn("queria o ok dele", rows(self.settings, "SELECT topic FROM pending_messages")[0]["topic"])
        m.approve_goal(goal["id"])
        self.run_all(m)
        self.assertEqual(rows(self.settings, "SELECT status FROM goals")[0]["status"], "done")

    def test_respects_the_active_cap_and_the_interval(self):
        cid, _ = self.dream(0)
        m = self.manager([ideas(idea("Anotar as ideias de vídeo dele", cycles=[cid]),
                                idea("Calcular uma rotina de edição semanal", memories=[self.mem]))], max_active=1, max_per_run=2)
        m.tick()
        self.assertEqual(len(rows(self.settings, "SELECT id FROM goals WHERE origin = 'nym'")), 1)   # cabe só 1
        conn = connect(self.settings.db_path)
        with transaction(conn):
            conn.execute("UPDATE goals SET status = 'cancelled'")
        conn.close()
        add_mem(self.settings, "O usuário comprou um microfone novo", minutes=30)
        before = len(self.provider.calls)
        m.tick()                                                 # mesmo com espaço: intervalo de 6 h não passou e não houve sonho novo
        self.assertEqual(len(m.desire_runs()), 1)
        self.assertEqual(len(self.provider.calls), before)

    def test_new_dream_wakes_him_before_the_interval(self):
        self.dream(0)
        m = self.manager([ideas(), ideas()])
        m.tick()
        self.assertEqual(m.desire_runs()[0]["status"], "nothing")   # nada o chamou: tudo bem
        self.dream(70)
        m2 = self.manager(["{\"goals\": []}"], minutes=80)
        m2.tick()
        self.assertEqual([r["trigger"] for r in m2.desire_runs()], ["dream", "dream"])

    def test_he_decides_to_investigate_pending_explorations(self):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            for q in ("O que ele já disse sobre o canal?", "Que equipamento ele tem?", "Quando ele grava?"):
                conn.execute("INSERT INTO exploration_tasks (question, refs, status, created_at, updated_at) "
                             "VALUES (?, '[]', 'pending', ?, ?)", (q, at(0), at(0)))
        conn.close()
        m = self.manager([], max_active=2)
        result = m.tick()
        self.assertEqual(len(result["approved_explorations"]), 2)     # só o que cabe
        self.assertEqual(self.provider.calls, [])                     # sem espaço sobrando, nem chamou o modelo
        statuses = [r["status"] for r in rows(self.settings, "SELECT status FROM exploration_tasks ORDER BY id")]
        self.assertEqual(statuses, ["approved", "approved", "pending"])

    def test_disabled_means_nothing_happens(self):
        self.dream(0)
        m = self.manager([ideas(idea("Anotar ideias", memories=[self.mem]))], enabled=False)
        self.assertIsNone(m.tick())
        self.assertEqual(self.provider.calls, [])

    def test_model_unavailable_is_recorded_without_goals(self):
        self.dream(0)
        m = self.manager([ModelUnavailableError("ollama fora")])
        m.tick()
        run = m.desire_runs()[0]
        self.assertEqual(run["status"], "failed")
        self.assertIn("indisponível", run["error"])
        self.assertEqual(rows(self.settings, "SELECT id FROM goals"), [])
        self.assertIsNone(m.tick())                                    # espera antes de tentar de novo

    def test_settings_validation(self):
        m = self.manager([])
        self.assertTrue(m.get_autonomy()["enabled"])
        for bad in ({"max_active": 0}, {"max_per_day": -1}, {"enabled": "sim"}, {"inventado": 1}, {"interval_hours": 999}):
            with self.assertRaises(TaskInputError, msg=str(bad)):
                m.update_autonomy(**bad)

    def test_chat_block_shows_it_was_his_decision(self):
        cid, _ = self.dream(0)
        m = self.manager([ideas(idea("Anotar as ideias de vídeo dele", cycles=[cid]))], auto_start=False)
        m.tick()
        block = m.block_for_chat()
        self.assertIn("ideia sua, esperando o ok dele", block)
        self.assertIn("motivo registrado: Sonhei com o canal", block)


class ParseTests(DesireBase):
    def test_requires_goals_field(self):
        ctx = {"memories": [], "dream_items": [], "cycles": [], "existing_goals": []}
        with self.assertRaises(desires.PlanError):
            desires.parse_desires("{}", ctx, 2)
        self.assertEqual(desires.parse_desires('{"goals": []}', ctx, 2).desires, [])
