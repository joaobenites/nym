import unittest
from datetime import timezone

from app.core.chat_service import ChatService
from app.core.state import StateService
from app.core.identity import IdentityService
from app.dreams.engine import DreamEngine
from app.dreams.journal import DreamJournal, asks_about_dreams
from app.memory.service import MemoryService
from tests.dreamtools import add_mem, dream_reply
from tests.fakes import prompt_of, DbTestCase, FakeProvider, ScriptedProvider
from app.database.connection import connect, transaction
from tests.memtools import at, clock_at


class JournalTests(DbTestCase):
    def test_detects_questions_about_dreams(self):
        for text in ("você sonhou com alguma coisa?", "Qual foi o último SONHO?", "teve sonhos esta noite", "sonhando acordado"):
            self.assertTrue(asks_about_dreams(text), text)
        for text in ("oi tudo bem", "como foi o trabalho hoje?"):
            self.assertFalse(asks_about_dreams(text), text)

    def test_no_block_unless_asked_and_honest_when_there_are_no_dreams(self):
        j = DreamJournal(self.settings)
        self.assertEqual(j.block_for_chat("oi tudo bem"), "")
        block = j.block_for_chat("você sonhou ontem?")
        self.assertIn("ainda não houve nenhum sonho registrado", block)
        self.assertIn("não invente", block)

    def test_block_carries_the_real_record(self):
        a = add_mem(self.settings, "O usuário tem um gato chamado Mia")
        b = add_mem(self.settings, "O usuário quer adotar outro gato")
        engine = DreamEngine(self.settings, ScriptedProvider([dream_reply(
            review="Revisão: o usuário pensa em ter mais um gato.",
            associations=[{"memories": [a, b], "text": "Os dois falam de gatos"}],
            questions=[{"text": "Quando será a adoção?", "evidence": [b]}])]), IdentityService(self.settings), clock=clock_at(0))
        engine.run_cycle("manual")
        block = DreamJournal(self.settings).block_for_chat("conta do seu último sonho")
        for expected in ("pensa em ter mais um gato", "ligação: Os dois falam de gatos", "pergunta em aberto: Quando será a adoção?", "registros reais"):
            self.assertIn(expected, block)

    def test_questions_and_exploration_can_be_managed_by_the_user(self):
        a = add_mem(self.settings, "O usuário estuda alemão")
        engine = DreamEngine(self.settings, ScriptedProvider([dream_reply(
            questions=[{"text": "Para que o usuário estuda alemão?", "evidence": [a]}],
            exploration=[{"question": "Quais métodos ajudam a aprender alemão?", "evidence": [a]}])]), IdentityService(self.settings), clock=clock_at(0))
        engine.run_cycle("manual")
        j = DreamJournal(self.settings)
        q = j.open_questions()[0]
        self.assertTrue(j.close_question(q["id"]))
        self.assertEqual(j.open_questions(), [])
        t = j.explorations("pending")[0]
        self.assertTrue(j.set_exploration(t["id"], "approved"))
        self.assertEqual(j.explorations("approved")[0]["id"], t["id"])
        self.assertTrue(j.set_exploration(t["id"], "dismissed"))
        with self.assertRaises(ValueError):
            j.set_exploration(t["id"], "done")        # só o usuário aprova ou dispensa; "done" virá com as ferramentas
        self.assertIsNone(j.get_cycle(999))

    def test_pause_flag_persists(self):
        DreamJournal(self.settings).set_paused(True)
        self.assertTrue(DreamJournal(self.settings).is_paused())


class ChatIntegrationTests(DbTestCase):
    def chat(self, provider):
        s = self.settings
        return ChatService(s, provider, memory=MemoryService(s), state=StateService(s), dreams=DreamJournal(s))

    def test_state_block_is_in_the_prompt_and_says_it_is_not_a_feeling(self):
        provider = FakeProvider()
        self.chat(provider).send_message("oi, tudo bem por aí hoje?")
        system = prompt_of(provider.calls[-1])
        self.assertIn("Seu estado interno agora", system)
        self.assertIn("não um sentimento", system)

    def test_dream_diary_only_appears_when_asked(self):
        provider = FakeProvider()
        chat = self.chat(provider)
        chat.send_message("oi, tudo bem por aí hoje?")
        self.assertNotIn("Seus sonhos (registros", prompt_of(provider.calls[-1]))
        self.assertNotIn("Sonho recente", prompt_of(provider.calls[-1]))   # não sonhou: nada de sonho no contexto
        chat.send_message("você sonhou com alguma coisa?")
        self.assertIn("Seus sonhos (registros", prompt_of(provider.calls[-1]))

    def test_fixed_rule_forbids_inventing_dreams(self):
        provider = FakeProvider()
        self.chat(provider).send_message("conta um sonho seu")
        system = prompt_of(provider.calls[-1])
        self.assertIn("nada além disso", system)
        self.assertIn("você não lembra de ter sonhado", system)
        self.assertIn("não lembra de ter sonhado; não invente", system)   # registro honesto: nenhum sonho ainda


class DreamSinceLastChatTests(DbTestCase):
    """O Nym sabe que sonhou enquanto o usuário estava fora, sem precisar que ele pergunte."""

    def dream_at(self, minutes, review):
        a = add_mem(self.settings, "O usuário quer começar a editar vídeos para o canal")
        engine = DreamEngine(self.settings, ScriptedProvider([dream_reply(review=review)]),
                             IdentityService(self.settings), clock=clock_at(minutes))
        cycle_id = engine.run_cycle("manual")["cycle_id"]
        conn = connect(self.settings.db_path)   # o motor grava o horário real; aqui fixamos o do relógio do teste
        try:
            with transaction(conn):
                conn.execute("UPDATE dream_cycles SET started_at = ?, ended_at = ? WHERE id = ?",
                             (at(minutes), at(minutes), cycle_id))
        finally:
            conn.close()
        return a

    def chat(self, provider, minutes):
        s = self.settings
        return ChatService(s, provider, memory=MemoryService(s), state=StateService(s, clock=clock_at(minutes)),
                           dreams=DreamJournal(s), clock=clock_at(minutes), tz=timezone.utc)

    def test_dream_after_last_chat_reaches_the_prompt_unasked(self):
        provider = FakeProvider()
        self.chat(provider, 0).send_message("vou dormir, até amanhã")
        self.dream_at(120, "Fiquei ligando o canal dele à vontade de editar vídeos.")
        self.chat(provider, 60 * 20).send_message("bom dia")
        system = prompt_of(provider.calls[-1])
        self.assertIn("Sonho recente: enquanto vocês estavam sem conversar, você sonhou", system)
        self.assertIn("ligando o canal dele", system)
        self.assertIn("você sonhou (ontem à tarde)", system)   # sonho às 12h de 1/out; conversa às 6h de 2/out (UTC)

    def test_dream_before_the_last_chat_is_not_news(self):
        provider = FakeProvider()
        self.dream_at(0, "Sonho antigo sobre vídeos.")
        self.chat(provider, 60).send_message("oi")
        self.chat(provider, 60 * 30).send_message("voltei")
        self.assertNotIn("Sonho recente", prompt_of(provider.calls[-1]))


if __name__ == "__main__":
    unittest.main()
