"""Fase 8.1: acesso pelo celular na rede local, só com aparelho pareado."""
import unittest

from app.core import access
from app.core.access import AccessGuard
from app.database.connection import connect
from tests.dreamtools import rows
from tests.fakes import DbTestCase
from tests.memtools import at

try:
    from fastapi.testclient import TestClient
    from app.main import create_app
    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover
    HAVE_FASTAPI = False


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class HelperTests(unittest.TestCase):
    def test_loopback_and_host(self):
        for ok in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "127.0.0.5"):
            self.assertTrue(access.is_loopback(ok), ok)
        for bad in ("192.168.0.10", "10.0.0.2", None, "", "nym.local"):
            self.assertFalse(access.is_loopback(bad), bad)
        self.assertEqual(access.host_name("127.0.0.1:8000"), "127.0.0.1")
        self.assertEqual(access.host_name("[::1]:8000"), "[::1]")
        self.assertEqual(access.host_name("LOCALHOST"), "localhost")
        self.assertNotIn(access.host_name("evil.example:8000"), access.LOCAL_HOSTS)


class GuardTests(DbTestCase):
    def guard(self):
        self.clock = Clock()
        return AccessGuard(lambda: connect(self.settings.db_path), lambda: at(0), clock=self.clock)

    def test_pairing_is_one_time_and_stores_only_a_hash(self):
        g = self.guard()
        code = g.new_code()["code"]
        self.assertEqual(len(code), 6)
        self.assertIsNone(g.pair("000000" if code != "000000" else "111111", "Celular", "192.168.0.9"))
        token = g.pair(code, "Celular do João", "192.168.0.9")
        self.assertTrue(token)
        self.assertIsNone(g.pair(code, "outro", "192.168.0.8"))            # uso único
        dev = rows(self.settings, "SELECT * FROM devices")[0]
        self.assertEqual(dev["token_hash"], access.hash_token(token))
        self.assertNotIn(token, str(dict(dev)))
        self.assertEqual(g.device_for(token, "192.168.0.9")["name"], "Celular do João")
        self.assertIsNone(g.device_for("token-falso", "192.168.0.9"))
        self.assertTrue(g.revoke(dev["id"]))
        self.assertIsNone(g.device_for(token, "192.168.0.9"))

    def test_code_expires_and_dies_after_too_many_tries(self):
        g = self.guard()
        code = g.new_code()["code"]
        self.clock.t += access.CODE_TTL + 1
        self.assertIsNone(g.pair(code, "x", "192.168.0.2"))
        code = g.new_code()["code"]
        wrong = "123456" if code != "123456" else "654321"
        for _ in range(access.CODE_TRIES):
            g.pair(wrong, "x", "192.168.0.3")
        self.assertIsNone(g.pair(code, "x", "192.168.0.4"))                 # depois de 5 erros nem o certo vale

    def test_ip_is_blocked_after_many_failures(self):
        g = self.guard()
        for _ in range(access.IP_FAILS):
            g.new_code()
            g.pair("999999", "x", "192.168.0.66")
        code = g.new_code()["code"]
        with self.assertRaises(PermissionError):
            g.pair(code, "x", "192.168.0.66")
        self.clock.t += access.IP_WINDOW + 1
        self.assertTrue(g.pair(g.new_code()["code"], "x", "192.168.0.66"))     # passou o bloqueio: volta a valer


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx não instalados")
class LanApiTests(DbTestCase):
    def client(self, ip):
        from tests.fakes import ScriptedProvider
        self.app = getattr(self, "app", None) or create_app(self.settings, ScriptedProvider(["oi"]))
        try:
            return TestClient(self.app, client=(ip, 50000))
        except TypeError:   # Starlette antigo sem o parâmetro client
            self.skipTest("TestClient sem suporte a client=")

    def test_phone_needs_pairing_and_cannot_pair_others(self):
        pc = self.client("127.0.0.1")
        phone = self.client("192.168.0.20")
        self.assertEqual(phone.get("/api/chat/history").status_code, 401)
        r = phone.get("/index.html", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/login.html"))
        self.assertEqual(phone.get("/login.html").status_code, 200)
        self.assertEqual(phone.post("/api/access/code").status_code, 401)
        code = pc.post("/api/access/code").json()["code"]
        self.assertEqual(phone.post("/api/pair", json={"code": "000000" if code != "000000" else "111111"}).status_code, 401)
        r = phone.post("/api/pair", json={"code": code, "name": "Celular"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(phone.get("/api/chat/history").status_code, 200)   # cookie guardado pelo cliente
        self.assertEqual(phone.post("/api/access/code").status_code, 403)   # pareado, mas não pareia outros
        dev = pc.get("/api/access").json()["devices"][0]
        pc.delete(f"/api/access/devices/{dev['id']}")
        self.assertEqual(phone.get("/api/chat/history").status_code, 401)

    def test_dns_rebinding_host_is_refused_on_loopback(self):
        pc = self.client("127.0.0.1")
        self.assertEqual(pc.get("/api/chat/history", headers={"host": "evil.example:8000"}).status_code, 403)
        self.assertEqual(pc.get("/api/chat/history", headers={"host": "127.0.0.1:8000"}).status_code, 200)
