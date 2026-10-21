"""Fase 6.2: lembretes de "perguntar depois" e a evolução (estágios) do Nym."""
import json
import unittest
from datetime import datetime, timedelta, timezone

from app.core import followups, growth
from app.core.chat_service import ChatService
from app.database.connection import connect, transaction
from app.memory.consolidation import Consolidator
from app.memory.extraction import parse_extraction
from tests.dreamtools import rows
from tests.fakes import DbTestCase, ScriptedProvider
from tests.memtools import BASE, at, clock_at, extraction, seed_chat
from tests.test_presence import PresenceBase


def with_followups(raw, items):
    data = json.loads(raw)
    data["followups"] = items
    return json.dumps(data, ensure_ascii=False)


class ExtractionTests(unittest.TestCase):
    def parse(self, items):
        return parse_extraction(with_followups(extraction(), items), message_ids={1, 2, 3}, user_ids={1, 3},
                                existing_ids=set()).followups

    def test_needs_user_evidence_and_a_valid_date(self):
        out = self.parse([
            {"text": "Perguntar como foi a prova de cálculo", "about": "prova", "due": "2026-10-09", "evidence": [1]},
            {"text": "Perguntar do jogo", "evidence": [2]},                       # 2 é fala do Nym: descartado
            {"text": "Perguntar da entrevista", "due": "sexta", "evidence": [3]},  # data ilegível vira None
        ])
        self.assertEqual([(f.text, f.due) for f in out],
                         [("Perguntar como foi a prova de cálculo", "2026-10-09"), ("Perguntar da entrevista", None)])

    def test_secrets_and_extra_items_are_dropped(self):
        out = self.parse([{"text": "Perguntar se a senha 123456 funcionou", "evidence": [1]}]
                         + [{"text": f"Perguntar da coisa número {i}", "evidence": [1]} for i in range(5)])
        self.assertTrue(all("senha" not in f.text for f in out))
        self.assertLessEqual(len(out), 2)

    def test_old_format_without_followups_still_parses(self):
        self.assertEqual(self.parse(None), [])


class StoreTests(DbTestCase):
    def conn(self):
        c = connect(self.settings.db_path)
        self.addCleanup(c.close)
        return c

    def test_due_time(self):
        said = datetime(2026, 10, 7, 22, 0, tzinfo=timezone.utc)
        self.assertEqual(followups.due_time("2026-10-09", said, timezone.utc), datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(followups.due_time(None, said, timezone.utc), said + timedelta(hours=20))
        # "hoje" já passou do meio-dia: nunca antes de 2 h depois de ele contar
        self.assertEqual(followups.due_time("2026-10-07", said, timezone.utc), said + timedelta(hours=2))

    def test_dedupe_due_expiry_and_delivery(self):
        c = self.conn()
        with transaction(c):
            a = followups.add(c, "Perguntar como foi a prova de cálculo", "prova", BASE, [1], None, at(0))
            self.assertIsNone(followups.add(c, "Perguntar como foi a prova de calculo", "prova", BASE, [1], None, at(0)))
            old = followups.add(c, "Perguntar da viagem para a praia", "viagem", BASE - timedelta(days=6), [1], None, at(0))
            later = followups.add(c, "Perguntar do show de sábado", "show", BASE + timedelta(days=2), [1], None, at(0))
        with transaction(c):
            due = followups.due(c, BASE + timedelta(hours=1))
        self.assertEqual([f["id"] for f in due], [a])
        status = {r["id"]: r["status"] for r in c.execute("SELECT id, status FROM followups")}
        self.assertEqual((status[old], status[later]), ("expired", "pending"))
        with transaction(c):
            followups.mark_delivered(c, a, at(70))
        self.assertEqual(followups.due(c, BASE + timedelta(hours=1)), [])

    def test_asked_in(self):
        item = {"text": "Perguntar como foi a prova de cálculo", "about": "prova"}
        self.assertTrue(followups.asked_in("E aí, como foi a prova de cálculo?", item))
        self.assertFalse(followups.asked_in("Bom dia! Dormiu bem?", item))


class ConsolidationTests(DbTestCase):
    def test_consolidation_stores_followups_with_evidence(self):
        ids = seed_chat(self.settings, [("user", "Tenho prova de cálculo na sexta, tô nervoso", 0), ("assistant", "Vai dar certo!", 1)])
        reply = with_followups(extraction("Ele contou da prova de cálculo."),
                               [{"text": "Perguntar como foi a prova de cálculo", "about": "prova", "due": "2026-10-02",
                                 "evidence": [ids[0]]}])
        Consolidator(self.settings, ScriptedProvider([reply]), clock=clock_at(60 * 24)).run_once()
        got = rows(self.settings, "SELECT text, status, evidence, due_at, episode_id FROM followups")
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["text"], got[0]["status"], json.loads(got[0]["evidence"])),
                         ("Perguntar como foi a prova de cálculo", "pending", [ids[0]]))
        self.assertTrue(got[0]["due_at"].startswith("2026-10-02"))
        self.assertIsNotNone(got[0]["episode_id"])


class DeliveryTests(PresenceBase):
    def setUp(self):
        super().setUp()
        seed_chat(self.settings, [("user", "tenho prova de cálculo amanhã", 0), ("assistant", "boa sorte!", 1)])
        c = connect(self.settings.db_path)
        with transaction(c):
            self.fid = followups.add(c, "Perguntar como foi a prova de cálculo", "prova", BASE + timedelta(minutes=10),
                                     [1], None, at(0))
        c.close()

    def test_presence_asks_once_and_marks_it_delivered(self):
        p = self.build(replies=["E aí, como foi a prova de cálculo?"], minutes=60)
        msg = p.tick()
        self.assertEqual(msg["kind"], "followup")
        self.assertIn("prova de cálculo", self.provider.calls[0][-1].content)
        self.assertEqual(rows(self.settings, "SELECT status FROM followups")[0]["status"], "delivered")
        self.assertIsNone(p.tick())   # não pergunta de novo

    def test_not_before_the_time(self):
        self.assertIsNone(self.build(minutes=5).tick())
        self.assertEqual(rows(self.settings, "SELECT status FROM followups")[0]["status"], "pending")

    def test_chat_reply_that_asks_marks_it_delivered(self):
        chat = ChatService(self.settings, ScriptedProvider(["Ah, e como foi a prova de cálculo?"]),
                           clock=lambda: BASE + timedelta(minutes=60), tz=timezone.utc)
        chat.send_message("oi, voltei")
        self.assertIn("prova de cálculo", chat._provider.calls[0][-1].content)
        self.assertEqual(rows(self.settings, "SELECT status FROM followups")[0]["status"], "delivered")

    def test_chat_reply_that_does_not_ask_keeps_it(self):
        chat = ChatService(self.settings, ScriptedProvider(["Oi! Tudo bem?"]),
                           clock=lambda: BASE + timedelta(minutes=60), tz=timezone.utc)
        chat.send_message("oi")
        self.assertEqual(rows(self.settings, "SELECT status FROM followups")[0]["status"], "pending")


class GrowthTests(DbTestCase):
    def conn(self):
        c = connect(self.settings.db_path)
        self.addCleanup(c.close)
        return c

    def test_starts_as_an_egg(self):
        info = growth.compute(self.conn())
        self.assertEqual((info["stage"], info["name"], info["xp"]), (0, "Ovo digital", 0))
        self.assertEqual(info["next_at"], 40)

    def test_xp_comes_from_real_records(self):
        # 3 dias diferentes com fala dele: 3*15 + 4 mensagens = 49 -> Filhote
        seed_chat(self.settings, [("user", "oi", 0), ("assistant", "oi!", 1), ("user", "voltei", 60 * 24),
                                  ("user", "de novo", 60 * 48), ("user", "mais uma", 60 * 48 + 5)])
        info = growth.compute(self.conn())
        self.assertEqual(info["counts"]["days"], 3)
        self.assertEqual(info["counts"]["user_messages"], 4)
        self.assertEqual((info["xp"], info["stage"], info["name"]), (49, 1, "Filhote"))
        self.assertAlmostEqual(info["progress"], round(9 / 160, 3))

    def test_check_advance_records_first_and_reports_only_ups(self):
        c = self.conn()
        with transaction(c):
            self.assertIsNone(growth.check_advance(c, at(0)))             # primeira vez: só registra
        seed_chat(self.settings, [("user", "oi", 0), ("user", "voltei", 60 * 24), ("user", "de novo", 60 * 48)])
        with transaction(c):
            change = growth.check_advance(c, at(1))
        self.assertEqual((change["from"], change["to"]), ("Ovo digital", "Filhote"))
        with transaction(c):
            self.assertIsNone(growth.check_advance(c, at(2)))             # sem subir de novo, sem aviso

    def test_stage_never_goes_down(self):
        c = self.conn()
        with transaction(c):
            c.execute("INSERT INTO app_state (key, value, updated_at) VALUES ('growth_stage', '3', ?)", (at(0),))
        self.assertEqual(growth.compute(c)["name"], "Parceiro")   # apagar dados não faz ele "desevoluir"


class GrowthPresenceTests(PresenceBase):
    def test_level_up_becomes_something_to_say_once(self):
        seed_chat(self.settings, [("user", "oi", 0), ("assistant", "oi!", 1)])
        c = connect(self.settings.db_path)
        with transaction(c):
            growth.check_advance(c, at(0))          # já registrado como ovo
        c.close()
        seed_chat(self.settings, [("user", "voltei", 60 * 24), ("user", "de novo", 60 * 48), ("assistant", "eba", 60 * 48 + 1)])
        p = self.build(replies=["Olha só, sinto que cresci um pouco."], minutes=60 * 48 + 30)
        msg = p.tick()
        self.assertEqual(msg["kind"], "growth")
        self.assertIn("Filhote", self.provider.calls[0][-1].content)
        self.assertEqual(rows(self.settings, "SELECT COUNT(*) AS n FROM pending_messages WHERE kind = 'growth'")[0]["n"], 1)


if __name__ == "__main__":
    unittest.main()
