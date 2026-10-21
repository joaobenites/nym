"""Testes da camada HTTP. Exigem fastapi e httpx (pip install -r requirements-dev.txt)."""
import unittest

try:
    from fastapi.testclient import TestClient
    from app.main import create_app
    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover
    HAVE_FASTAPI = False

from app.models.base import ModelUnavailableError
from tests.dreamtools import dream_reply
from tests.fakes import DbTestCase, FakeProvider, ScriptedProvider


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx não instalados")
class ApiTests(DbTestCase):
    def client(self, provider=None):
        app = create_app(self.settings, provider or FakeProvider())
        return TestClient(app)

    def test_health(self):
        data = self.client().get("/health").json()
        self.assertTrue(data["database"]["ok"])
        self.assertTrue(data["model"]["available"])

    def test_chat_and_history(self):
        c = self.client()
        self.assertEqual(c.get("/api/chat/history").json(), {"messages": [], "has_more": False})
        r = c.post("/api/chat", json={"message": "oi"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["reply"], "eco: oi")
        c.post("/api/chat", json={"message": "tudo bem?"})
        msgs = c.get("/api/chat/history").json()["messages"]
        self.assertEqual([m["content"] for m in msgs], ["oi", "eco: oi", "tudo bem?", "eco: tudo bem?"])

    def test_there_are_no_multi_conversation_routes(self):
        c = self.client()
        self.assertEqual(c.get("/api/conversations").status_code, 404)

    def test_model_unavailable_returns_503_and_keeps_message(self):
        c = self.client(FakeProvider(fail_with=ModelUnavailableError("fora")))
        r = c.post("/api/chat", json={"message": "guarde isto"})
        self.assertEqual(r.status_code, 503)
        self.assertTrue(r.json()["retryable"])
        msgs = c.get("/api/chat/history").json()["messages"]
        self.assertEqual(msgs[0]["content"], "guarde isto")

    def test_validation_errors(self):
        c = self.client()
        self.assertEqual(c.post("/api/chat", json={"message": ""}).status_code, 422)
        self.assertEqual(c.post("/api/chat", json={"message": "   "}).status_code, 400)

    def test_history_survives_app_restart(self):
        self.client().post("/api/chat", json={"message": "persistir"})
        c2 = self.client()  # nova aplicação sobre o mesmo diretório de dados
        self.assertEqual(c2.get("/api/chat/history").json()["messages"][0]["content"], "persistir")

    # --- Fase 2: identidade e objetivos ---------------------------------------
    def test_identity_snapshot_and_user_edit(self):
        c = self.client()
        snap = c.get("/api/identity").json()
        self.assertIn("Nym", [e["value"] for e in snap["entries"] if e["kind"] == "name"])
        r = c.post("/api/identity/entries", json={"kind": "preference", "value": "prefere café", "justification": "teste"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "applied")
        changes = c.get("/api/identity/changes").json()
        self.assertEqual(changes[0]["new_value"], "prefere café")

    def test_identity_errors(self):
        c = self.client()
        name_id = next(e["id"] for e in c.get("/api/identity").json()["entries"] if e["kind"] == "name")
        self.assertEqual(c.patch(f"/api/identity/entries/{name_id}", json={"value": "Echo"}).status_code, 403)
        self.assertEqual(c.delete(f"/api/identity/entries/{name_id}").status_code, 403)
        self.assertEqual(c.post("/api/identity/entries", json={"kind": "rule", "value": "x"}).status_code, 400)
        self.assertEqual(c.patch("/api/identity/entries/99999", json={"value": "x"}).status_code, 404)

    def test_accept_and_reject_proposals(self):
        app = create_app(self.settings, FakeProvider())
        c = TestClient(app)
        p1 = app.state.identity.propose(kind="interest", action="add", value="astronomia", justification="x")
        p2 = app.state.identity.propose(kind="interest", action="add", value="pintura", justification="x")
        self.assertEqual(c.post(f"/api/identity/changes/{p1['id']}/accept").json()["status"], "accepted")
        self.assertEqual(c.post(f"/api/identity/changes/{p2['id']}/reject").json()["status"], "rejected")
        self.assertEqual(c.post(f"/api/identity/changes/{p1['id']}/accept").status_code, 409)
        self.assertEqual(c.post("/api/identity/changes/99999/accept").status_code, 404)

    def test_proposals_cannot_be_created_over_http(self):
        c = self.client()
        for path in ("/api/identity/proposals", "/api/identity/changes"):
            self.assertIn(c.post(path, json={"kind": "trait", "value": "x"}).status_code, (404, 405))

    def test_goals(self):
        c = self.client()
        g = c.post("/api/goals", json={"description": "aprender algo", "priority": 2}).json()
        self.assertEqual(g["status"], "active")
        self.assertEqual(c.patch(f"/api/goals/{g['id']}", json={"status": "done"}).json()["status"], "done")
        self.assertEqual(c.post("/api/goals", json={"description": "x", "priority": 9}).status_code, 400)
        self.assertEqual(c.patch("/api/goals/999", json={"status": "done"}).status_code, 404)

    # --- Fase 3: memória ----------------------------------------------------------
    def test_memory_crud_and_route_order(self):
        c = self.client()
        created = c.post("/api/memories", json={"content": "O usuário nasceu em março"}).json()
        self.assertEqual((created["origin"], created["epistemic"]), ("user_edit", "confirmed"))
        self.assertEqual(c.post("/api/memories", json={"content": "O usuário nasceu em março."}).status_code, 400)
        # rotas fixas não podem ser capturadas por /{memory_id}
        self.assertEqual(c.get("/api/memories/jobs").status_code, 200)
        self.assertEqual(c.get("/api/memories/retrieve", params={"q": "nasceu março"}).status_code, 200)
        self.assertEqual(c.get("/api/memories/identity-candidates").json(), [])
        got = c.get(f"/api/memories/{created['id']}").json()
        self.assertEqual(got["sources"][0]["source_type"], "manual")
        upd = c.patch(f"/api/memories/{created['id']}", json={"epistemic": "unverified"}).json()
        self.assertEqual(upd["epistemic"], "unverified")
        self.assertEqual(c.post(f"/api/memories/{created['id']}/archive").json()["status"], "archived")
        self.assertEqual(c.get("/api/memories").json(), [])
        self.assertEqual(len(c.get("/api/memories", params={"status": "archived"}).json()), 1)
        self.assertEqual(c.post(f"/api/memories/{created['id']}/restore").json()["status"], "active")

    def test_memory_errors_and_delete(self):
        c = self.client()
        self.assertEqual(c.get("/api/memories/999").status_code, 404)
        self.assertEqual(c.patch("/api/memories/999", json={"content": "x"}).status_code, 404)
        self.assertEqual(c.get("/api/memories", params={"kind": "inventado"}).status_code, 400)
        self.assertEqual(c.post("/api/memories", json={"content": "x", "kind": "episodic"}).status_code, 400)
        mid = c.post("/api/memories", json={"content": "O usuário coleciona selos"}).json()["id"]
        self.assertEqual(c.delete(f"/api/memories/{mid}").status_code, 200)
        self.assertEqual(c.get(f"/api/memories/{mid}").status_code, 404)

    def test_chat_reports_memories_used_and_consolidate_endpoint(self):
        c = self.client()
        c.post("/api/memories", json={"content": "O usuário tem um gato chamado Mia"})
        r = c.post("/api/chat", json={"message": "como está meu gato Mia hoje?"}).json()
        self.assertTrue(r["memories_used"])
        report = c.post("/api/memories/consolidate", json={"force": True}).json()
        self.assertEqual(report["status"], "ok")

    # --- Fase 4: sonhos, estado interno e presença ------------------------------------------------
    def test_state_endpoint_says_what_it_is(self):
        data = self.client().get("/api/state").json()
        for key in ("energy", "curiosity", "bond", "label", "reasons", "note"):
            self.assertIn(key, data)
        self.assertIn("não é sentimento", data["note"])

    def test_presence_settings_and_validation(self):
        c = self.client()
        self.assertEqual(c.get("/api/presence/settings").json()["max_per_day"], 0)   # sem limite por padrão (Fase 5)
        out = c.put("/api/presence/settings", json={"max_per_day": 1, "quiet_start": "22:00"}).json()
        self.assertEqual((out["max_per_day"], out["quiet_start"]), (1, "22:00"))
        self.assertEqual(c.put("/api/presence/settings", json={"quiet_start": "25:99"}).status_code, 400)
        self.assertEqual(c.put("/api/presence/settings", json={"max_per_day": 500}).status_code, 400)

    def test_tick_is_silent_when_there_is_nothing_to_say(self):
        c = self.client()
        c.post("/api/chat", json={"message": "oi, tudo bem por aí hoje?"})
        self.assertEqual(c.post("/api/presence/tick", json={"opened": True}).json(), {"message": None})

    def test_dreams_overview_routes_and_errors(self):
        c = self.client()
        d = c.get("/api/dreams").json()
        self.assertIn("status", d)
        self.assertEqual(d["cycles"], [])
        self.assertEqual(c.get("/api/dreams/questions").json(), [])        # rota fixa não é capturada por /{cycle_id}
        self.assertEqual(c.get("/api/dreams/exploration").json(), [])
        self.assertEqual(c.get("/api/dreams/9999").status_code, 404)
        self.assertEqual(c.post("/api/dreams/questions/9999/close").status_code, 404)
        self.assertEqual(c.post("/api/dreams/exploration/1/executar").status_code, 400)
        self.assertEqual(c.post("/api/dreams/cancel").json(), {"cancelled": False})
        self.assertEqual(c.post("/api/dreams/pause").json(), {"paused": True})
        self.assertTrue(c.get("/api/dreams").json()["status"]["paused"])
        self.assertEqual(c.post("/api/dreams/resume").json(), {"paused": False})

    def test_manual_dream_with_nothing_to_review_is_skipped(self):
        self.assertEqual(self.client().post("/api/dreams/run").json()["status"], "skipped")

    def test_manual_dream_completes_and_appears_in_the_diary(self):
        app = create_app(self.settings, ScriptedProvider([dream_reply(review="Revisão pela API.")]))
        c = TestClient(app)
        c.post("/api/memories", json={"content": "O usuário gosta de ler romances"})
        out = c.post("/api/dreams/run").json()
        self.assertEqual(out["status"], "completed")
        detail = c.get(f"/api/dreams/{out['cycle_id']}").json()
        self.assertEqual(detail["review"], "Revisão pela API.")
        self.assertEqual(len(c.get("/api/dreams").json()["cycles"]), 1)

    def test_a_model_that_does_not_return_json_fails_the_dream_cleanly(self):
        c = self.client()                                                  # FakeProvider responde "eco: ..." (não é JSON)
        c.post("/api/memories", json={"content": "O usuário gosta de ler romances"})
        self.assertEqual(c.post("/api/dreams/run").json()["status"], "failed")
        self.assertEqual(len(c.get("/api/memories").json()), 1)            # a memória original segue intacta

    def test_history_marks_initiative_messages(self):
        c = self.client()
        c.post("/api/chat", json={"message": "oi, tudo bem por aí hoje?"})
        self.assertEqual({m["initiative"] for m in c.get("/api/chat/history").json()["messages"]}, {0})


if __name__ == "__main__":
    unittest.main()
