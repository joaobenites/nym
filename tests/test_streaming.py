"""Velocidade (Fase 6): resposta em streaming, interromper de verdade e prompt com a parte fixa primeiro."""
from app.core.chat_service import ChatService, InvalidInputError
from app.core.identity import IdentityService
from app.models.base import LLMProvider, LLMResponse, ModelError, ModelUnavailableError, ProviderStatus, StreamEvent
from tests.dreamtools import rows
from tests.fakes import DbTestCase, FakeProvider


class Streamer(LLMProvider):
    """Gera os pedaços dados; pode falhar no meio. Registra se o stream foi fechado (o Ollama pararia de gerar)."""
    name = "streamer"
    model = "stream-1"

    def __init__(self, pieces, fail_after=None, error=None):
        self.pieces = list(pieces)
        self.fail_after = fail_after
        self.error = error or ModelError("caiu no meio")
        self.closed = False
        self.calls = []

    def chat(self, messages, *, temperature=None, json_mode=False):
        return LLMResponse("".join(self.pieces), self.model)

    def stream_chat(self, messages, *, temperature=None):
        self.calls.append(list(messages))
        try:
            for i, piece in enumerate(self.pieces):
                if self.fail_after is not None and i == self.fail_after:
                    raise self.error
                yield StreamEvent(text=piece)
            yield StreamEvent(done=True, stats={"eval_count": 7, "tokens_per_s": 35.0})
        finally:
            self.closed = True

    def check_availability(self):
        return ProviderStatus(True, "ok")


class StreamingTests(DbTestCase):
    def test_pieces_arrive_in_order_and_the_reply_is_saved(self):
        chat = ChatService(self.settings, Streamer(["Opa", ", tudo ", "certo? 😅"]))
        events = list(chat.stream_message("oi"))
        self.assertEqual(events[0]["type"], "start")
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "delta"), "Opa, tudo certo? ")
        done = events[-1]
        self.assertEqual((done["type"], done["reply"]), ("done", "Opa, tudo certo?"))   # emoji fora, texto limpo
        self.assertEqual(done["timing"]["tokens_per_s"], 35.0)
        history = chat.get_history()["messages"]
        self.assertEqual([m["content"] for m in history], ["oi", "Opa, tudo certo?"])

    def test_stop_keeps_what_was_said_and_closes_the_model_stream(self):
        provider = Streamer(["Deixa ", "eu ", "pensar ", "numa ", "coisa"])
        chat = ChatService(self.settings, provider)
        events = []
        for e in chat.stream_message("me conta algo"):
            events.append(e)
            if e["type"] == "delta" and len(events) == 3:
                chat.stop()
        self.assertEqual(events[-1]["type"], "stopped")
        self.assertEqual(events[-1]["reply"], "Deixa eu")
        self.assertTrue(provider.closed)
        self.assertEqual(chat.get_history()["messages"][-1]["content"], "Deixa eu")

    def test_browser_closing_mid_answer_saves_the_partial_reply(self):
        provider = Streamer(["Primeira ", "parte ", "e o resto"])
        chat = ChatService(self.settings, provider)
        gen = chat.stream_message("oi")
        next(gen)            # start
        next(gen)            # primeiro pedaço
        gen.close()          # a conexão HTTP caiu
        self.assertTrue(provider.closed)
        self.assertEqual(chat.get_history()["messages"][-1]["content"], "Primeira")

    def test_model_failure_mid_answer(self):
        chat = ChatService(self.settings, Streamer(["Começou ", "e"], fail_after=1, error=ModelUnavailableError("fora")))
        events = list(chat.stream_message("oi"))
        self.assertEqual(events[-1]["type"], "error")
        self.assertTrue(events[-1]["retryable"])
        msgs = chat.get_history()["messages"]
        self.assertEqual([m["content"] for m in msgs], ["oi", "Começou"])        # nada do que saiu se perde

    def test_empty_message_is_rejected_before_streaming(self):
        with self.assertRaises(InvalidInputError):
            ChatService(self.settings, Streamer(["x"])).stream_message("   ")

    def test_providers_without_streaming_still_work(self):
        events = list(ChatService(self.settings, FakeProvider()).stream_message("oi"))
        self.assertEqual(events[-1]["reply"], "eco: oi")

    def test_timing_is_audited(self):
        list(ChatService(self.settings, Streamer(["oi"])).stream_message("oi"))
        audit = rows(self.settings, "SELECT detail FROM audit_log WHERE event = 'chat.reply'")[-1]["detail"]
        self.assertIn('"tokens_per_s": 35.0', audit)
        self.assertIn('"server_s"', audit)


class CacheFriendlyPromptTests(DbTestCase):
    def test_system_prompt_is_identical_between_messages(self):
        # A parte fixa precisa ser byte a byte igual para o Ollama reaproveitar o processamento.
        provider = Streamer(["ok"])
        chat = ChatService(self.settings, provider)
        list(chat.stream_message("primeira"))
        list(chat.stream_message("segunda"))
        first, second = provider.calls
        self.assertEqual(first[0].content, second[0].content)
        self.assertEqual(first[0].content, IdentityService(self.settings).build_system_prompt())
        self.assertNotIn("Tempo:", first[0].content)
        self.assertEqual(second[1].content, "primeira")          # falas antigas vão limpas: prefixo estável
        self.assertIn("Tempo: Agora é", second[-1].content)      # o que muda vai só na última
