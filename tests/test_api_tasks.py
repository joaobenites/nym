"""Rotas da Fase 5. Exigem fastapi e httpx (pip install -r requirements-dev.txt)."""
import json
import unittest

try:
    from fastapi.testclient import TestClient
    from app.main import create_app
    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover
    HAVE_FASTAPI = False

from tests.fakes import DbTestCase, ScriptedProvider


def plan(*steps):
    return json.dumps({"steps": [{"title": t, "tool": tool, "args": args} for t, tool, args in steps]})


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx não instalados")
class TaskApiTests(DbTestCase):
    def setup_app(self, replies=()):
        self.app = create_app(self.settings, ScriptedProvider(list(replies)))
        return TestClient(self.app)          # sem "with": os trabalhadores de fundo não sobem; o teste chama tick()

    def test_create_plan_approve_and_finish(self):
        c = self.setup_app([plan(("anotar", "write_note", {"title": "ideias", "content": "vídeo sobre café"})),
                            json.dumps({"done": True, "summary": "Anotei as ideias.", "steps": []})])
        r = c.post("/api/tasks/goals", json={"description": "Anotar ideias de vídeo", "completion_criteria": "nota criada"})
        self.assertEqual(r.status_code, 200)
        goal_id = r.json()["id"]
        self.app.state.tasks.tick()                                    # planeja
        self.app.state.tasks.tick()                                    # pede permissão
        overview = c.get("/api/tasks").json()
        waiting = overview["awaiting_permission"]
        self.assertEqual(len(waiting), 1)
        self.assertIn("write_note", [t["name"] for t in overview["tools"]])
        self.assertEqual(c.post(f"/api/tasks/{waiting[0]['id']}/approve").json()["status"], "queued")
        while self.app.state.tasks.tick():
            pass
        goal = c.get(f"/api/tasks/goals/{goal_id}").json()
        self.assertEqual(goal["status"], "done")
        self.assertEqual(goal["result"], "Anotei as ideias.")
        self.assertTrue(goal["events"])

    def test_errors(self):
        c = self.setup_app()
        self.assertEqual(c.post("/api/tasks/goals", json={"description": "x", "priority": 9}).status_code, 400)
        self.assertEqual(c.post("/api/tasks/goals", json={"description": "x", "deadline": "amanhã"}).status_code, 400)
        self.assertEqual(c.get("/api/tasks/goals/999").status_code, 404)
        self.assertEqual(c.post("/api/tasks/999/approve").status_code, 404)
        self.assertEqual(c.post("/api/tasks/1/explode").status_code, 400)
        self.assertEqual(c.put("/api/tasks/permissions/calculate", json={"mode": "talvez"}).status_code, 400)
        self.assertEqual(c.put("/api/tasks/permissions/nao_existe", json={"mode": "auto"}).status_code, 400)

    def test_permissions_pause_and_cancel(self):
        c = self.setup_app([plan(("conta", "calculate", {"expression": "1+1"}))])
        tools = c.put("/api/tasks/permissions/calculate", json={"mode": "deny"}).json()
        self.assertEqual(next(t for t in tools if t["name"] == "calculate")["mode"], "deny")
        self.assertTrue(c.post("/api/tasks/pause").json()["paused"])
        goal_id = c.post("/api/tasks/goals", json={"description": "Somar"}).json()["id"]
        self.assertIsNone(self.app.state.tasks.tick())                # pausado: nada anda
        self.assertFalse(c.post("/api/tasks/resume").json()["paused"])
        self.assertEqual(c.post(f"/api/tasks/goals/{goal_id}/cancel").json()["status"], "cancelled")
        self.assertEqual(c.post(f"/api/tasks/goals/{goal_id}/cancel").status_code, 400)

    def test_autonomy_settings_and_approving_his_idea(self):
        c = self.setup_app()
        data = c.get("/api/tasks/autonomy").json()
        self.assertTrue(data["settings"]["enabled"])
        self.assertEqual(data["runs"], [])
        out = c.put("/api/tasks/autonomy", json={"auto_start": False, "max_active": 2}).json()
        self.assertEqual((out["auto_start"], out["max_active"]), (False, 2))
        self.assertEqual(c.put("/api/tasks/autonomy", json={"max_active": 0}).status_code, 400)
        self.assertEqual(c.post("/api/tasks/goals/999/approve").status_code, 404)
        mine = c.post("/api/tasks/goals", json={"description": "Somar"}).json()
        self.assertEqual(c.post(f"/api/tasks/goals/{mine['id']}/approve").status_code, 400)   # só ideias propostas dele
        self.assertIn("autonomy", c.get("/api/tasks").json())

    def test_folders_and_research_routes(self):
        import tempfile
        c = self.setup_app()
        with tempfile.TemporaryDirectory() as folder:
            f = c.post("/api/tasks/folders", json={"path": folder, "label": "ideias"}).json()
            self.assertEqual(f["label"], "ideias")
            self.assertEqual(c.post("/api/tasks/folders", json={"path": folder}).status_code, 400)        # repetida
            self.assertEqual(c.post("/api/tasks/folders", json={"path": "relativa"}).status_code, 400)
            self.assertEqual([x["label"] for x in c.get("/api/tasks/folders").json()], ["ideias"])
            self.assertTrue(c.delete(f"/api/tasks/folders/{f['id']}").json()["removed"])
            self.assertEqual(c.delete(f"/api/tasks/folders/{f['id']}").status_code, 404)
        self.assertEqual(c.get("/api/tasks/research").json(), [])

    def test_chat_stream_route(self):
        from tests.fakes import FakeProvider
        app = create_app(self.settings, FakeProvider())
        c = TestClient(app)
        r = c.post("/api/chat/stream", json={"message": "oi"})
        self.assertEqual(r.status_code, 200)
        events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
        self.assertEqual([e["type"] for e in events][0], "start")
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["reply"], "eco: oi")
        self.assertEqual(c.post("/api/chat/stream", json={"message": "   "}).status_code, 400)
        self.assertTrue(c.post("/api/chat/stop").json()["stopped"])

    def test_old_goal_routes_still_create_context_only_goals(self):
        c = self.setup_app()
        g = c.post("/api/goals", json={"description": "conhecer melhor o usuário"}).json()
        self.assertEqual(g["status"], "active")
        self.assertEqual(c.get("/api/tasks").json()["goals"], [])   # não vira tarefa


if __name__ == "__main__":
    unittest.main()
