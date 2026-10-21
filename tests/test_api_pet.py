"""Rotas dos cuidados do vpet (Fase 10). Exigem fastapi e httpx."""
import unittest

try:
    from fastapi.testclient import TestClient
    from app.main import create_app
    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover
    HAVE_FASTAPI = False

from tests.fakes import DbTestCase, ScriptedProvider


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx não instalados")
class PetApiTests(DbTestCase):
    def test_status_actions_and_errors(self):
        c = TestClient(create_app(self.settings, ScriptedProvider([])))
        first = c.get("/api/pet").json()
        self.assertIn("não é sofrimento", first["note"])
        self.assertIn("mood", first)
        r = c.post("/api/pet/pet", json={}).json()
        self.assertTrue(r["ok"])
        self.assertGreaterEqual(r["pet"]["affection"], first["affection"])
        played = c.post("/api/pet/play", json={"choice": "papel"}).json()
        self.assertIn(played["game"]["result"], ("voce", "nym", "empate"))
        self.assertEqual(c.post("/api/pet/play", json={"choice": "lagarto"}).status_code, 400)
        self.assertEqual(c.post("/api/pet/voar", json={}).status_code, 404)
        self.assertEqual(c.post("/api/pet/sleep", json={"hours": 99}).status_code, 400)
        self.assertTrue(c.post("/api/pet/pause", json={}).json()["pet"]["paused"])
        self.assertFalse(c.post("/api/pet/feed", json={}).json()["ok"])
        self.assertFalse(c.post("/api/pet/resume", json={}).json()["pet"]["paused"])
