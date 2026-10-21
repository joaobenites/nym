"""Rotas dos gostos do Nym (Fase 7.1). Exigem fastapi e httpx."""
import unittest

try:
    from fastapi.testclient import TestClient
    from app.main import create_app
    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover
    HAVE_FASTAPI = False

from app.database.connection import connect, transaction
from tests.fakes import DbTestCase, ScriptedProvider
from tests.memtools import at


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx não instalados")
class TasteApiTests(DbTestCase):
    def test_list_veto_restore_and_errors(self):
        c = TestClient(create_app(self.settings, ScriptedProvider([])))
        first = c.get("/api/tastes").json()
        self.assertEqual(first["tastes"], [])
        self.assertIn("não é prova", first["note"])
        conn = connect(self.settings.db_path)
        with transaction(conn):
            tid = conn.execute(
                "INSERT INTO tastes (key, label, valence, strength, score, items, days, sources, origin, status, first_seen, last_seen, updated_at) "
                "VALUES ('astron', 'astronomia', 1, 0.8, 8, 6, 3, '{}', 'own', 'active', ?, ?, ?)", (at(0), at(0), at(0))).lastrowid
        conn.close()
        self.assertEqual(c.post(f"/api/tastes/{tid}/veto").json(), {"id": tid, "status": "vetoed"})
        listed = c.get("/api/tastes").json()["tastes"]
        self.assertEqual([(t["label"], t["status"]) for t in listed], [("astronomia", "vetoed")])
        self.assertEqual(c.post(f"/api/tastes/{tid}/restore").json()["status"], "faded")
        self.assertEqual(c.post("/api/tastes/999/veto").status_code, 404)
        self.assertEqual(c.post("/api/tastes/999/restore").status_code, 404)
