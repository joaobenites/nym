"""Rotas da Fase 8 (sentidos). Exigem fastapi e httpx."""
import unittest

try:
    from fastapi.testclient import TestClient
    from app.main import create_app
    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover
    HAVE_FASTAPI = False

from tests.fakes import DbTestCase, ScriptedProvider
from tests.test_senses import PNG_B64, Eyes


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx não instalados")
class SensesApiTests(DbTestCase):
    def test_status_without_vision_refuses_images(self):
        c = TestClient(create_app(self.settings, ScriptedProvider(["oi"])))
        s = c.get("/api/senses").json()
        self.assertFalse(s["vision"]["available"])
        self.assertIn("yt_dlp", s["video"])
        r = c.post("/api/chat", json={"message": "olha", "images": [{"kind": "camera", "data": PNG_B64}]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(c.get("/api/chat/history").json()["messages"], [])
        self.assertEqual(c.delete("/api/perceptions/1").status_code, 404)

    def test_chat_with_image_and_forget(self):
        c = TestClient(create_app(self.settings, Eyes(replies=["Que gato!"])))
        r = c.post("/api/chat", json={"message": "olha", "images": [{"kind": "image", "data": PNG_B64}]})
        self.assertEqual(r.status_code, 200)
        p = c.get("/api/perceptions").json()["perceptions"][0]
        self.assertEqual(p["kind"], "image")
        self.assertEqual(c.get("/api/chat/history").json()["messages"][0]["perceptions"][0]["id"], p["id"])
        self.assertEqual(c.delete(f"/api/perceptions/{p['id']}").json(), {"deleted": p["id"]})
        self.assertNotIn("perceptions", c.get("/api/chat/history").json()["messages"][0])
