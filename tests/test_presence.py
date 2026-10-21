import unittest
from datetime import datetime, timedelta, timezone

from app.core.activity import ActivityGate
from app.core.chat_service import ChatService
from app.core.identity import IdentityService
from app.core.presence import PresenceInputError, PresenceService, add_candidate, in_quiet_hours
from app.core.state import StateService
from app.database import repository as repo
from app.database.connection import connect, transaction
from app.memory.service import MemoryService
from tests.dreamtools import rows
from tests.fakes import DbTestCase, FakeProvider, ScriptedProvider
from tests.memtools import BASE, at, seed_chat


class Clock:
    def __init__(self, minutes=0.0):
        self.minutes = minutes

    def __call__(self):
        return BASE + timedelta(minutes=self.minutes)


class PresenceBase(DbTestCase):
    def build(self, replies=("Oi! Fiquei pensando numa coisa que você disse.",), minutes=30.0, provider=None, **overrides):
        settings = self.make_settings(**overrides)
        self.clock = Clock(minutes)
        self.provider = provider or ScriptedProvider(list(replies))
        self.gate = ActivityGate()
        return PresenceService(settings, self.provider, IdentityService(settings), MemoryService(settings),
                               StateService(settings, clock=self.clock), self.gate, clock=self.clock, tz=timezone.utc)

    def note(self, topic="O usuário comentou que quer adotar outro gato"):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            nid = add_candidate(conn, "dream_note", topic, [], None)
        conn.close()
        return nid

    def initiative_count(self):
        return rows(self.settings, "SELECT COUNT(*) AS n FROM messages WHERE initiative = 1")[0]["n"]


class SpeakingTests(PresenceBase):
    def setUp(self):
        super().setUp()
        seed_chat(self.settings, [("user", "falamos sobre gatos hoje de manhã", 0), ("assistant", "legal!", 1)])

    def test_speaks_about_a_dream_note_and_marks_it_as_initiative(self):
        nid = self.note()
        msg = self.build(minutes=30).tick()
        self.assertEqual((msg["kind"], msg["role"], msg["initiative"]), ("dream_note", "assistant", 1))
        self.assertEqual(self.initiative_count(), 1)
        self.assertEqual(rows(self.settings, "SELECT status FROM pending_messages WHERE id = ?", (nid,))[0]["status"], "delivered")
        history = ChatService(self.settings, FakeProvider()).get_history()["messages"]
        self.assertEqual(history[-1]["initiative"], 1)

    def test_the_prompt_is_grounded_and_the_instruction_is_not_stored(self):
        self.note("O usuário comentou que quer adotar outro gato")
        p = self.build(minutes=30)
        msg = p.tick()
        system, *chat, instruction = self.provider.calls[0]
        self.assertEqual(system.role, "system")
        self.assertIn("Seu estado interno", system.content)
        self.assertEqual(instruction.role, "user")
        self.assertIn("quer adotar outro gato", instruction.content)
        self.assertIn("Instrução interna", instruction.content)
        self.assertNotIn("Instrução interna", msg["content"])
        self.assertNotIn("Instrução interna", " ".join(r["content"] for r in rows(self.settings, "SELECT content FROM messages")))

    def test_cleans_the_text(self):
        self.note()
        msg = self.build(replies=['"Nym: E aí, decidiu sobre o gato?"']).tick()
        self.assertEqual(msg["content"], "E aí, decidiu sobre o gato?")

    def test_welcome_back_needs_the_app_to_be_opened_and_enough_absence(self):
        p = self.build(minutes=60 * 5)            # 5 h depois (limite: 4 h)
        self.assertIsNone(p.tick(opened=False))
        msg = p.tick(opened=True)
        self.assertEqual(msg["kind"], "welcome_back")
        self.assertIn("5 hora", self.provider.calls[0][-1].content)
        p2 = self.build(minutes=60 * 2)
        self.assertIsNone(p2.tick(opened=True))   # 2 h só: ainda não é "voltou"

    def test_nothing_to_say_means_silence(self):
        self.assertIsNone(self.build(minutes=60).tick(opened=False))
        self.assertEqual(self.initiative_count(), 0)


class RestraintTests(PresenceBase):
    """Tudo o que impede o Nym de ser inconveniente."""

    def setUp(self):
        super().setUp()
        seed_chat(self.settings, [("user", "falamos sobre gatos hoje de manhã", 0), ("assistant", "legal!", 1)])
        self.note()

    def test_disabled(self):
        p = self.build(presence_enabled=False)
        self.assertIsNone(p.tick())
        p = self.build()
        p.update_settings(enabled=False)
        self.assertIsNone(p.tick())
        self.assertEqual(self.provider.calls, [])

    def test_quiet_hours(self):
        self.assertIsNone(self.build(minutes=13 * 60 + 30).tick())          # 23:30 UTC
        self.assertEqual(self.provider.calls, [])

    def test_not_right_after_the_user_wrote(self):
        self.assertIsNone(self.build(minutes=1.5).tick())   # 30 s depois da última fala (minuto 1)

    def test_not_while_the_chat_is_busy(self):
        p = self.build()
        with self.gate.chat():
            self.assertIsNone(p.tick())

    def test_welcome_back_does_not_repeat_without_a_reply(self):
        p = self.build(replies=["Primeira.", "Segunda."])
        self.assertIsNotNone(p.tick())
        self.clock.minutes = 60 * 6
        self.assertIsNone(p.tick(opened=True))           # sem motivo novo: o "oi, voltou" não se repete sem resposta
        self.assertEqual(self.initiative_count(), 1)

    def test_no_limit_by_default_each_real_reason_is_delivered(self):
        # Pedido do dono (Fase 5): ele pode chamar sempre que tiver motivo, mesmo sem resposta à anterior.
        p = self.build(replies=["Primeira.", "Segunda.", "Terceira."])
        self.assertEqual((p.get_settings()["max_per_day"], p.get_settings()["min_gap_minutes"]), (0, 0))
        self.assertIsNotNone(p.tick())
        for i, topic in enumerate(("Pendência sobre viagens de trem", "Pendência sobre receitas de bolo"), start=1):
            self.note(topic)
            self.clock.minutes = 30 + 2 * i
            self.assertIsNotNone(p.tick())
        self.assertEqual(self.initiative_count(), 3)
        self.assertIsNone(p.tick())                      # nada pendente: não fala por falar

    def test_technical_floor_between_initiatives(self):
        p = self.build(replies=["Primeira.", "Segunda."])
        self.assertIsNotNone(p.tick())
        self.note("Pendência sobre viagens de trem")
        self.clock.minutes = 30.5                        # 30 s depois: abaixo do piso de 1 minuto
        self.assertIsNone(p.tick())
        self.clock.minutes = 32
        self.assertIsNotNone(p.tick())

    def test_task_notes_come_first(self):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            conn.execute("INSERT INTO pending_messages (kind, topic, refs, status, created_at) "
                         "VALUES ('task_done', 'Terminei o objetivo \"anotar a lista\".', '[]', 'pending', ?)", (at(0),))
        conn.close()
        self.note()
        msg = self.build().tick()
        self.assertEqual(msg["kind"], "task_done")
        self.assertIn("tarefa sua que terminou", self.provider.calls[-1][-1].content)

    def test_daily_cap_and_minimum_gap(self):
        p = self.build(replies=["Primeira.", "Segunda.", "Terceira."], presence_max_per_day=2, presence_min_gap_minutes=90)
        self.assertIsNotNone(p.tick())
        seed_chat(self.settings, [("user", "respondi à sua mensagem agora mesmo", 40)])
        self.note("Outra pendência totalmente diferente sobre viagens")
        self.clock.minutes = 60                                             # 30 min depois da iniciativa: < 90
        self.assertIsNone(p.tick())
        self.clock.minutes = 200                                            # intervalo ok
        self.assertIsNotNone(p.tick())
        seed_chat(self.settings, [("user", "respondi de novo para você", 210)])
        self.note("Mais uma pendência sobre outro assunto qualquer")
        self.clock.minutes = 400
        self.assertIsNone(p.tick())                                         # 2 por dia: acabou

    def test_stale_dream_notes_expire(self):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            conn.execute("UPDATE pending_messages SET created_at = ?", (at(0),))
        conn.close()
        p = self.build(minutes=60 * 24 * 4)                                 # 4 dias depois (validade: 3)
        self.assertIsNone(p.tick(opened=False))
        self.assertEqual(rows(self.settings, "SELECT status FROM pending_messages")[0]["status"], "expired")

    def test_model_failure_sends_nothing_and_keeps_the_topic(self):
        from app.models.base import ModelUnavailableError
        p = self.build(replies=[ModelUnavailableError("fora do ar")])
        self.assertIsNone(p.tick())
        self.assertEqual(self.initiative_count(), 0)
        self.assertEqual(rows(self.settings, "SELECT status FROM pending_messages")[0]["status"], "pending")

    def test_discards_the_message_if_the_user_spoke_while_it_was_being_written(self):
        settings = self.settings

        class UserInterrupts(ScriptedProvider):
            def chat(self, messages, **kw):
                conn = connect(settings.db_path)
                with transaction(conn):
                    cid = repo.get_state(conn, "main_conversation_id")
                    repo.add_message(conn, cid, "user", "ei, cheguei agora mesmo")
                conn.close()
                return super().chat(messages, **kw)

        p = self.build(provider=UserInterrupts(["Oi, tudo bem?"]))
        self.assertIsNone(p.tick())
        self.assertEqual(self.initiative_count(), 0)
        self.assertEqual(rows(self.settings, "SELECT status FROM pending_messages")[0]["status"], "pending")


class SettingsTests(PresenceBase):
    def test_defaults_update_and_persistence(self):
        p = self.build()
        self.assertEqual(p.get_settings()["max_per_day"], 0)          # sem limite por padrão
        out = p.update_settings(quiet_start="22:30", max_per_day=1, enabled=False)
        self.assertEqual((out["quiet_start"], out["max_per_day"], out["enabled"]), ("22:30", 1, False))
        self.assertEqual(self.build().get_settings()["quiet_start"], "22:30")   # persistiu

    def test_validation(self):
        p = self.build()
        for bad in ({"quiet_start": "25:00"}, {"quiet_end": "abc"}, {"max_per_day": -1}, {"max_per_day": 99},
                    {"min_gap_minutes": "x"}, {"enabled": "sim"}, {"welcome_back_hours": 0}, {"inventado": 1}):
            with self.assertRaises(PresenceInputError, msg=str(bad)):
                p.update_settings(**bad)

    def test_quiet_hours_wrap_midnight(self):
        at_ = lambda h, m=0: datetime(2026, 1, 1, h, m, tzinfo=timezone.utc)
        self.assertTrue(in_quiet_hours(at_(23, 30), "23:00", "08:00"))
        self.assertTrue(in_quiet_hours(at_(3), "23:00", "08:00"))
        self.assertFalse(in_quiet_hours(at_(8), "23:00", "08:00"))
        self.assertFalse(in_quiet_hours(at_(14), "23:00", "08:00"))
        self.assertTrue(in_quiet_hours(at_(13), "12:00", "14:00"))
        self.assertFalse(in_quiet_hours(at_(3), "10:00", "10:00"))        # início = fim: sem horário de silêncio

    def test_candidates_are_deduplicated_and_capped(self):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            self.assertIsNotNone(add_candidate(conn, "dream_note", "Perguntar sobre a viagem para a praia", [], None))
            self.assertIsNone(add_candidate(conn, "dream_note", "Perguntar sobre a viagem para a praia!", [], None))
            for t in ("assunto totalmente diferente número dois", "terceiro assunto sem relação alguma aqui", "quarto tema muito distinto de tudo"):
                add_candidate(conn, "dream_note", t, [], None)
        conn.close()
        pending = rows(self.settings, "SELECT topic FROM pending_messages WHERE status = 'pending'")
        self.assertEqual(len(pending), 3)                                  # a fila é limitada; o mais antigo expira


if __name__ == "__main__":
    unittest.main()
