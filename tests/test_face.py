"""Fase 10: rosto reativo. Estado de base só do código; reação escolhida pelo Nym ([[rosto: ...]]) ou lida da fala do dono."""
import unittest
from datetime import timedelta

from app.core import commitments, face
from app.core.chat_service import ChatService
from app.core.policy import OPERATIONAL_RULES
from app.database.connection import connect, transaction
from tests.dreamtools import rows
from tests.fakes import DbTestCase, ScriptedProvider
from tests.memtools import BASE
from tests.test_streaming import Streamer

try:
    from fastapi.testclient import TestClient
    from app.main import create_app
    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover
    HAVE_FASTAPI = False


class ParseTests(unittest.TestCase):
    def test_tag_is_read_with_aliases_and_unknown_words_are_ignored(self):
        self.assertEqual(face.extract("Sério?!\n[[rosto: surpresa]]"), "surpreso")
        self.assertEqual(face.extract("[[ Rosto : Apaixonado ]]"), "carinho")      # "apaixonado" = olhos de coração
        self.assertEqual(face.extract("[[rosto: bravo]]"), "irritado")
        self.assertIsNone(face.extract("[[rosto: explodindo]]"))
        self.assertIsNone(face.extract("sem marca nenhuma"))
        self.assertEqual(face.extract("[[rosto: feliz]] ... [[rosto: risada]]"), "risada")   # vale a última

    def test_tag_never_shows_to_him_even_while_streaming(self):
        raw = "Hahaha, essa foi boa.\n[[rosto: risada]]"
        self.assertEqual(commitments.visible_text(raw, final=True), "Hahaha, essa foi boa.")
        self.assertEqual(commitments.visible_text("Hahaha, essa foi boa.\n[[ros"), "Hahaha, essa foi boa.\n")
        both = "Bora!\n[[fazer: desenhar um lobo]]\n[[rosto: feliz]]"
        self.assertEqual(commitments.visible_text(both, final=True), "Bora!")
        self.assertEqual(commitments.extract_tags(both), ["desenhar um lobo"])

    def test_reading_his_message_when_nym_did_not_choose(self):
        self.assertEqual(face.from_user_text("kkkkkk"), "risada")
        self.assertEqual(face.from_user_text("te amo, nym"), "carinho")
        self.assertEqual(face.from_user_text("seu inútil"), "irritado")
        self.assertEqual(face.from_user_text("adivinha o que aconteceu"), "surpreso")
        self.assertEqual(face.from_user_text("consegui o emprego"), "feliz")
        self.assertEqual(face.from_user_text("tô triste hoje"), "triste")
        self.assertIsNone(face.from_user_text("ok"))
        self.assertEqual(face.react("Que isso!\n[[rosto: assustado]]", "kkkk"), ("assustado", "model"))   # escolha dele vence
        self.assertEqual(face.react("Hahaha", "kkkk"), ("risada", "dono"))
        self.assertIsNone(face.react("Beleza.", "ok"))

    def test_base_state_priority(self):
        self.assertEqual(face.base_state({}), "neutro")
        self.assertEqual(face.base_state({"dreaming": True, "waiting": True}), "sonhando")
        self.assertEqual(face.base_state({"pet_sleeping": True, "working": True}), "dormindo")
        self.assertEqual(face.base_state({"waiting": True, "energy": 0.1}), "esperando")
        self.assertEqual(face.base_state({"energy": 0.1, "working": True}), "cansado")
        self.assertEqual(face.base_state({"working": True, "curiosity": 0.9}), "trabalhando")
        self.assertEqual(face.base_state({"curiosity": 0.9}), "curioso")

    def test_policy_tells_him_he_may_choose_and_that_it_is_expression(self):
        rule = next(r for r in OPERATIONAL_RULES if "[[rosto:" in r)
        for word in face.REACTIONS:
            if word != "neutro":
                self.assertIn(word, rule)
        self.assertIn("não prova de sentimento", rule)


class RecordTests(DbTestCase):
    def test_reaction_lasts_a_few_seconds_over_the_real_state(self):
        conn = connect(self.settings.db_path)
        try:
            with transaction(conn):
                face.record(conn, "surpreso", "model", None, BASE)
            snap = face.snapshot(conn, BASE + timedelta(seconds=3), {})
            self.assertEqual((snap["state"], snap["reaction"]["expression"]), ("neutro", "surpreso"))
            self.assertIn("não é prova de sentimento", snap["note"])
            later = face.snapshot(conn, BASE + timedelta(seconds=face.REACTION_SECONDS + 5), {"energy": 0.1})
            self.assertIsNone(later["reaction"])
            self.assertEqual(later["state"], "cansado")
            with self.assertRaises(ValueError):
                face.record(conn, "explodindo", "model", None, BASE)
        finally:
            conn.close()


class ChatTests(DbTestCase):
    def test_his_choice_is_hidden_saved_and_returned(self):
        svc = ChatService(self.settings, ScriptedProvider(["Não acredito! Sério mesmo?\n[[rosto: surpresa]]"]))
        result = svc.send_message("passei na prova de direção")
        self.assertEqual(result.reply, "Não acredito! Sério mesmo?")
        self.assertEqual(result.face, {"expression": "surpreso", "source": "model"})
        saved = rows(self.settings, "SELECT expression, source, message_id FROM face_events")
        self.assertEqual(saved, [{"expression": "surpreso", "source": "model", "message_id": result.assistant_message_id}])
        self.assertNotIn("[[", rows(self.settings, "SELECT content FROM messages WHERE id = ?", (result.assistant_message_id,))[0]["content"])

    def test_streaming_hides_the_tag_and_the_done_event_carries_the_face(self):
        svc = ChatService(self.settings, Streamer(["Ha", "haha, essa foi boa.", "\n[[ros", "to: risada]]"]))
        events = list(svc.stream_message("kkkkk olha esse meme"))
        shown = "".join(e["text"] for e in events if e["type"] == "delta")
        self.assertNotIn("[[", shown)
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["reply"], "Hahaha, essa foi boa.")
        self.assertEqual(done["face"], {"expression": "risada", "source": "model"})

    def test_without_his_choice_the_code_reads_the_message(self):
        result = ChatService(self.settings, ScriptedProvider(["Hehe, valeu!"])).send_message("te adoro, nym")
        self.assertEqual(result.face, {"expression": "carinho", "source": "dono"})
        none = ChatService(self.settings, ScriptedProvider(["Beleza."])).send_message("ok")
        self.assertIsNone(none.face)


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx não instalados")
class FaceApiTests(DbTestCase):
    def test_get_face(self):
        c = TestClient(create_app(self.settings, ScriptedProvider(["Uau!\n[[rosto: surpreso]]"])))
        first = c.get("/api/face").json()
        self.assertIn(first["state"], face.STATES)
        self.assertIsNone(first["reaction"])
        c.post("/api/chat", json={"message": "adivinha"})
        after = c.get("/api/face").json()
        self.assertEqual(after["reaction"]["expression"], "surpreso")


if __name__ == "__main__":
    unittest.main()
