"""Fase 10.2 — achados da conversa real de 22/10 (como "Claudio"):

- "Pesquisa pra mim qual foi o primeiro vpet?" (ordem terminada em "?") não virou objetivo; na cobrança ele disse "Já descobri!";
- "kkkkkk" respondido com a MESMA frase de horas antes;
- desabafo respondido com menu de opções ("quer ficar de boa ou prefere falar de algo?").
"""
import unittest

from app.core import requests as req
from app.core import rhythm
from app.core.chat_service import ChatService
from app.core.voice import find_issues, repeats_recent
from app.models.base import LLMProvider, LLMResponse, ProviderStatus, StreamEvent
from app.tasks.manager import TaskManager
from app.tools.registry import ToolRegistry
from tests.fakes import DbTestCase, ScriptedProvider


class ImperativeQuestionTests(unittest.TestCase):
    def test_real_phrase_is_a_request(self):
        got = req.explicit_request("Oi Nym, aqui é o Claudio (testando). Pesquisa pra mim qual foi o primeiro vpet da história?")
        self.assertIn("primeiro vpet", got)

    def test_descriptions_and_plain_questions_are_not(self):
        for t in ("você pesquisa isso sozinho?", "qual a melhor forma de pesquisar vpets?", "ele busca coisas novas toda noite?",
                  "não pesquisa isso agora", "se você pesquisar algo me avisa"):
            self.assertIsNone(req.explicit_request(t), t)

    def test_polite_forms_still_work(self):
        self.assertIsNotNone(req.explicit_request("pode pesquisar o primeiro vpet da história?"))


class NoRequestTests(DbTestCase):
    def test_asking_progress_without_any_goal_never_gets_a_result_story(self):
        class P(LLMProvider):
            name, model = "p", "p-1"
            def chat(self, messages, *, temperature=None, json_mode=False):
                return LLMResponse("Já descobri! Foi o Dogz.", self.model)
            def stream_chat(self, messages, *, temperature=None):
                self.ctx = list(messages)
                yield StreamEvent(text="Já descobri! Foi o Dogz.")
                yield StreamEvent(done=True, stats={})
            def check_availability(self):
                return ProviderStatus(True, "ok")
        provider = P()
        tasks = TaskManager(self.settings, ScriptedProvider([]), ToolRegistry())
        chat = ChatService(self.settings, provider, tasks=tasks)
        events = list(chat.stream_message("e aí, já descobriu?"))
        self.assertIn("NÃO existe nenhum pedido", provider.ctx[-1].content)
        self.assertIn("acao_inventada", events[-2]["issues"] if events[-1]["type"] == "correction" else events[-1]["issues"])


class RepeatTests(unittest.TestCase):
    OLD = "Pelo menos eu tentei manter a dignidade da Angewomon no meio da piada. Se eu continuar praticando, quem sabe um dia eu consigo."

    def test_copy_is_caught(self):
        self.assertTrue(repeats_recent(self.OLD, ["oi", self.OLD]))

    def test_one_long_sentence_copied_is_caught(self):
        self.assertTrue(repeats_recent("Rá. Pelo menos eu tentei manter a dignidade da Angewomon no meio da piada.", [self.OLD]))

    def test_new_short_line_is_fine(self):
        self.assertFalse(repeats_recent("Rindo aqui também.", [self.OLD]))
        self.assertFalse(repeats_recent("ok", ["ok"]))   # curtíssimo demais para julgar

    def test_it_is_a_voice_issue_not_an_honesty_one(self):
        from app.core.voice import HONESTY_ISSUES
        self.assertIn("repetida", find_issues("kkkkkk", self.OLD, history_replies=[self.OLD]))
        self.assertNotIn("repetida", HONESTY_ISSUES)


class MenuTests(unittest.TestCase):
    def test_menu_after_a_vent_is_flagged(self):
        r = "Poxa, sinto muito. Quer só ficar de boa aqui um tempo ou prefere falar sobre algo pra distrair a cabeça?"
        self.assertIn("menu_de_opcoes", find_issues("hoje foi um dia péssimo, tô muito cansado", r))

    def test_menu_is_fine_outside_a_vent(self):
        r = "Quer ver agora ou prefere depois?"
        self.assertNotIn("menu_de_opcoes", find_issues("achei um vídeo legal", r))

    def test_plain_welcome_passes(self):
        self.assertNotIn("menu_de_opcoes", find_issues("tô muito cansado", "Putz, dia pesado. Tô aqui."))


class CarefulStreamTests(DbTestCase):
    def test_laugh_gets_a_second_take_when_the_first_is_a_copy(self):
        old = "Pelo menos eu tentei manter a dignidade da Angewomon no meio da piada."

        class P(LLMProvider):
            name, model = "p", "p-1"
            def __init__(self):
                self.chats = []
            def chat(self, messages, *, temperature=None, json_mode=False):
                self.chats.append(messages)
                return LLMResponse("Haha, essa me pegou.", self.model)
            def stream_chat(self, messages, *, temperature=None):
                yield StreamEvent(text=old)
                yield StreamEvent(done=True, stats={})
            def check_availability(self):
                return ProviderStatus(True, "ok")
        provider = P()
        chat = ChatService(self.settings, provider)
        chat._send_message("conta uma piada")   # abre a conversa
        from app.database.connection import connect
        conn = connect(self.settings.db_path)
        from app.database import repository as repo
        cid = chat._main_conversation(conn)
        repo.add_message(conn, cid, "assistant", old, model="x")
        conn.close()
        events = list(chat.stream_message("kkkkkk"))
        shown = "".join(e["text"] for e in events if e["type"] == "delta")
        self.assertEqual(shown, "Haha, essa me pegou.")
        self.assertEqual(len(provider.chats) >= 1, True)
        self.assertEqual(events[-1]["reply"], "Haha, essa me pegou.")

    def test_normal_messages_still_stream_piece_by_piece(self):
        self.assertFalse(rhythm.needs_care("me conta uma coisa"))
        self.assertFalse(rhythm.needs_care("oi"))
        for t in ("kkkkk", "tô cansado", "vou nessa"):
            self.assertTrue(rhythm.needs_care(t), t)


if __name__ == "__main__":
    unittest.main()
