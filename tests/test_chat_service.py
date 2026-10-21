import unittest

from app.core.chat_service import ChatFailure, ChatService, InvalidInputError
from app.core.identity import IdentityService
from app.database.connection import connect
from app.models.base import ModelError, ModelUnavailableError
from tests.fakes import spoken, DbTestCase, FakeProvider


def contents(history):
    return [(m["role"], m["content"]) for m in history["messages"]]


class SingleChatTests(DbTestCase):
    def test_history_survives_new_service_instance(self):
        ChatService(self.settings, FakeProvider()).send_message("olá, Nym")
        second = ChatService(self.settings, FakeProvider())  # "reinício"
        self.assertEqual(contents(second.get_history()),
                         [("user", "olá, Nym"), ("assistant", "eco: olá, Nym")])

    def test_everything_goes_into_one_continuous_chat(self):
        svc = ChatService(self.settings, FakeProvider())
        svc.send_message("um")
        svc.send_message("dois")
        self.assertEqual(len(svc.get_history()["messages"]), 4)
        conn = connect(self.settings.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], 1)
        conn.close()

    def test_empty_history_before_first_message(self):
        self.assertEqual(ChatService(self.settings, FakeProvider()).get_history(),
                         {"messages": [], "has_more": False})

    def test_model_unavailable_preserves_user_message(self):
        provider = FakeProvider(fail_with=ModelUnavailableError("fora do ar"))
        svc = ChatService(self.settings, provider)
        with self.assertRaises(ChatFailure) as ctx:
            svc.send_message("preciso disso salvo")
        self.assertTrue(ctx.exception.unavailable)
        self.assertEqual(contents(svc.get_history()), [("user", "preciso disso salvo")])
        conn = connect(self.settings.db_path)
        events = [r["event"] for r in conn.execute("SELECT event FROM audit_log")]
        conn.close()
        self.assertIn("chat.model_failure", events)

    def test_recovers_after_model_returns(self):
        provider = FakeProvider(fail_with=ModelUnavailableError("fora do ar"))
        svc = ChatService(self.settings, provider)
        with self.assertRaises(ChatFailure):
            svc.send_message("primeira")
        provider.fail_with = None
        result = svc.send_message("segunda")
        sent = [spoken(m.content) for m in provider.calls[-1] if m.role == "user"]
        self.assertEqual(sent, ["primeira", "segunda"])  # a mensagem perdida voltou ao contexto
        self.assertEqual(result.reply, "eco: segunda")

    def test_generic_model_error_is_not_flagged_unavailable(self):
        svc = ChatService(self.settings, FakeProvider(fail_with=ModelError("resposta ruim")))
        with self.assertRaises(ChatFailure) as ctx:
            svc.send_message("oi")
        self.assertFalse(ctx.exception.unavailable)

    def test_switching_model_keeps_history(self):
        ChatService(self.settings, FakeProvider(model="modelo-a")).send_message("pergunta 1")
        b_provider = FakeProvider(model="modelo-b")
        b = ChatService(self.settings, b_provider)
        b.send_message("pergunta 2")
        models = [m["model"] for m in b.get_history()["messages"] if m["role"] == "assistant"]
        self.assertEqual(models, ["modelo-a", "modelo-b"])
        self.assertIn("eco: pergunta 1", [m.content for m in b_provider.calls[-1]])

    def test_context_respects_message_limit_and_system_prompt_is_not_stored(self):
        provider = FakeProvider()
        svc = ChatService(self.make_settings(context_messages=4), provider)
        for i in range(6):
            svc.send_message(f"m{i}")
        last_call = provider.calls[-1]
        self.assertEqual(last_call[0].role, "system")
        self.assertEqual(last_call[0].content, IdentityService(self.settings).build_system_prompt())  # só a parte fixa
        self.assertIn("Tempo: Agora é", last_call[-1].content)          # o que muda vai junto da última fala
        self.assertIn("no meio de uma conversa", last_call[-1].content)
        self.assertIn(len(last_call) - 1, (4, 5))                         # janela de N a N + bloco - 1
        self.assertEqual(spoken(last_call[-1].content), "m5")
        self.assertNotIn("system", [m["role"] for m in svc.get_history()["messages"]])

    def test_window_start_stays_put_for_several_messages(self):
        # O começo do histórico só muda em blocos: o Ollama reaproveita o processamento das falas antigas.
        provider = FakeProvider()
        svc = ChatService(self.make_settings(context_messages=6), provider)
        for i in range(12):
            svc.send_message(f"m{i}")
        firsts = [spoken(call[1].content) for call in provider.calls]
        changes = sum(1 for a, b in zip(firsts, firsts[1:]) if a != b)
        self.assertLessEqual(changes, 12 // 3 + 1)                       # mudou poucas vezes, não a cada mensagem
        for call in provider.calls:
            self.assertLessEqual(len(call) - 1, 6 + 3 - 1)

    def test_context_respects_char_budget_but_keeps_newest(self):
        provider = FakeProvider()
        svc = ChatService(self.make_settings(context_chars=200), provider)
        svc.send_message("a" * 150)
        svc.send_message("b" * 150)
        texts = [spoken(m.content) for m in provider.calls[-1] if m.role != "system"]
        self.assertEqual(texts[-1], "b" * 150)
        self.assertNotIn("a" * 150, texts)

    def test_invalid_input_creates_nothing(self):
        svc = ChatService(self.settings, FakeProvider())
        for bad in ["", "   \n ", "x" * 9000]:
            with self.assertRaises(InvalidInputError):
                svc.send_message(bad)
        self.assertEqual(svc.get_history()["messages"], [])
        conn = connect(self.settings.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], 0)
        conn.close()

    def test_history_pagination(self):
        svc = ChatService(self.settings, FakeProvider())
        for i in range(5):
            svc.send_message(f"m{i}")  # 10 mensagens no total
        page1 = svc.get_history(limit=4)
        self.assertTrue(page1["has_more"])
        self.assertEqual([m["content"] for m in page1["messages"]], ["m3", "eco: m3", "m4", "eco: m4"])
        page2 = svc.get_history(limit=4, before_id=page1["messages"][0]["id"])
        self.assertTrue(page2["has_more"])
        self.assertEqual([m["content"] for m in page2["messages"]], ["m1", "eco: m1", "m2", "eco: m2"])
        page3 = svc.get_history(limit=4, before_id=page2["messages"][0]["id"])
        self.assertFalse(page3["has_more"])
        self.assertEqual([m["content"] for m in page3["messages"]], ["m0", "eco: m0"])
        self.assertEqual(len(svc.get_history(limit=500)["messages"]), 10)


class UpgradeFromMultiConversationTests(unittest.TestCase):
    """Bancos das Fases 1 e 2 têm várias conversas: a mais recente vira o chat único."""

    def test_most_recent_becomes_the_chat_and_others_are_kept(self):
        import shutil
        import tempfile
        from pathlib import Path

        from app.config import Settings
        from app.database.migrator import MIGRATIONS_DIR, apply_migrations

        with tempfile.TemporaryDirectory() as tmp:
            old_migs = Path(tmp) / "old"
            old_migs.mkdir()
            for name in ("001_conversations_messages.sql", "002_identity_goals.sql"):
                shutil.copy(MIGRATIONS_DIR / name, old_migs)
            settings = Settings(data_dir=Path(tmp) / "data")
            conn = connect(settings.db_path)
            apply_migrations(conn, old_migs)
            conn.execute("INSERT INTO conversations VALUES ('velha','teste antigo','2026-01-01','2026-01-01')")
            conn.execute("INSERT INTO conversations VALUES ('nova','conversa atual','2026-02-01','2026-03-01')")
            for cid, text in (("velha", "mensagem velha"), ("nova", "mensagem nova")):
                conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, model, created_at) "
                    "VALUES (?, 'user', ?, NULL, 't')", (cid, text))
            apply_migrations(conn, MIGRATIONS_DIR)  # aplica 003
            conn.close()

            history = ChatService(settings, FakeProvider()).get_history()
            self.assertEqual([m["content"] for m in history["messages"]], ["mensagem nova"])
            conn = connect(settings.db_path)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)  # nada foi apagado
            conn.close()
            # a conversa nova continua o chat principal e recebe as próximas mensagens
            ChatService(settings, FakeProvider()).send_message("segue")
            conn = connect(settings.db_path)
            n = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id='nova'").fetchone()[0]
            conn.close()
            self.assertEqual(n, 3)


if __name__ == "__main__":
    unittest.main()
