from datetime import timedelta

from app.core import growth, pet
from app.core.chat_service import ChatService
from app.database.connection import connect, transaction
from tests.dreamtools import rows
from tests.fakes import FakeProvider, prompt_of
from tests.memtools import BASE, seed_chat
from tests.test_presence import PresenceBase


def make_hungry(settings, when):
    conn = connect(settings.db_path)
    with transaction(conn):
        pet.advance(conn, when)
        conn.execute("UPDATE pet_state SET fullness = 10, updated_at = ?", (when.isoformat(timespec="milliseconds"),))
    conn.close()


class ChatBlockTests(PresenceBase):
    def test_chat_context_has_the_pet_block_with_real_state(self):
        seed_chat(self.settings, [("user", "oi", 0), ("assistant", "e aí", 1)])
        provider = FakeProvider()
        svc = ChatService(self.settings, provider, clock=lambda: BASE + timedelta(minutes=30))
        make_hungry(self.settings, BASE + timedelta(minutes=29))
        svc.send_message("tudo bem?")
        text = prompt_of(provider.calls[-1])
        self.assertIn("Seu corpinho de vpet", text)
        self.assertIn("com fome", text)
        self.assertIn("chamando por cuidado (fome)", text)

    def test_policy_has_the_vpet_rule_in_the_fixed_prompt(self):
        from app.core.policy import OPERATIONAL_RULES
        self.assertIn("parceiro virtual", " ".join(OPERATIONAL_RULES))
        self.assertIn("corpinho de vpet", " ".join(OPERATIONAL_RULES))


class PresenceCallTests(PresenceBase):
    def test_hungry_pet_calls_once_and_feeding_cancels_a_pending_call(self):
        seed_chat(self.settings, [("user", "falamos de gatos", 0), ("assistant", "legal!", 1)])
        make_hungry(self.settings, BASE + timedelta(minutes=29))
        p = self.build(minutes=30)
        msg = p.tick()
        self.assertEqual(msg["kind"], "pet_call")
        self.assertIn("vpet", self.provider.calls[0][-1].content)
        self.assertEqual(p.tick(), None)       # entregue uma vez
        self.assertEqual(rows(self.settings, "SELECT status FROM pending_messages WHERE kind = 'pet_call'")[0]["status"], "delivered")

    def test_feeding_before_delivery_dismisses_the_call(self):
        seed_chat(self.settings, [("user", "oi", 0), ("assistant", "e aí", 1)])
        make_hungry(self.settings, BASE + timedelta(minutes=29))
        conn = connect(self.settings.db_path)
        with transaction(conn):
            pet.advance(conn, BASE + timedelta(minutes=30))
            pet.queue_calls(conn, BASE + timedelta(minutes=30))
            pet.act(conn, "feed", BASE + timedelta(minutes=31))
        conn.close()
        self.assertEqual(rows(self.settings, "SELECT status FROM pending_messages WHERE kind = 'pet_call'")[0]["status"], "dismissed")
        self.assertIsNone(self.build(minutes=35).tick())


class GrowthTests(PresenceBase):
    def test_care_days_give_xp(self):
        conn = connect(self.settings.db_path)
        before = growth.compute(conn)["xp"]
        with transaction(conn):
            pet.act(conn, "pet", BASE)
            pet.act(conn, "pet", BASE + timedelta(days=1))
        info = growth.compute(conn)
        conn.close()
        self.assertEqual(info["counts"]["care_days"], 2)
        self.assertEqual(info["xp"] - before, 2 * growth.WEIGHTS["care_days"])
