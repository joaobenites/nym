"""Rotas da Fase 7 (ateliê, propostas, lições, reações). Exigem fastapi e httpx."""
import unittest

try:
    from fastapi.testclient import TestClient
    from app.main import create_app
    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover
    HAVE_FASTAPI = False

from app.creations import store
from app.database.connection import connect, transaction
from tests.fakes import DbTestCase, ScriptedProvider
from tests.memtools import at, seed_chat


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx não instalados")
class AtelierApiTests(DbTestCase):
    def test_creations_feedback_lessons_and_errors(self):
        c = TestClient(create_app(self.settings, ScriptedProvider([])))
        conn = connect(self.settings.db_path)
        with transaction(conn):
            cid = store.save(conn, "text", "Poema", "um poema", "Um poema de verdade, com versos e tudo mais.", at(0))["id"]
        conn.close()
        self.assertEqual(c.get("/api/creations").json()["creations"][0]["id"], cid)
        self.assertIn("versos", c.get(f"/api/creations/{cid}").json()["content"])
        self.assertEqual(c.get("/api/creations/999").status_code, 404)
        self.assertEqual(c.get("/api/creations?kind=escultura").status_code, 400)
        self.assertEqual(c.post("/api/feedback", json={"target": "creation", "id": cid, "value": 1}).status_code, 200)
        self.assertEqual(c.get("/api/creations").json()["creations"][0]["reaction"], 1)
        self.assertEqual(c.post("/api/feedback", json={"target": "creation", "id": cid, "value": 5}).status_code, 400)
        ids = seed_chat(self.settings, [("user", "oi", 0), ("assistant", "oi!", 1)])
        self.assertEqual(c.post("/api/feedback", json={"target": "message", "id": ids[1], "value": -1}).status_code, 200)
        self.assertEqual(c.get("/api/chat/history").json()["messages"][-1]["reaction"], -1)
        self.assertEqual(c.post(f"/api/creations/{cid}/archive").json()["status"], "archived")
        self.assertEqual(c.get("/api/lessons").json(), {"lessons": []})
        self.assertEqual(c.get("/api/proposals").json(), {"proposals": []})
        self.assertEqual(c.post("/api/proposals/1/apply").status_code, 404)
        self.assertEqual(c.post("/api/proposals/1/hack").status_code, 404)
