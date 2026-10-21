"""Fase 6.1: o que o Nym combina na conversa vira trabalho de verdade; a marca nunca aparece para o usuário."""
import json
import time
import unittest

from app.core import commitments
from app.core.chat_service import ChatService
from app.models.base import LLMProvider, LLMResponse, ProviderStatus, StreamEvent
from app.tasks.manager import TaskManager
from app.tools.registry import ToolRegistry
from tests.dreamtools import rows
from tests.fakes import DbTestCase, ScriptedProvider


class Talker(LLMProvider):
    """Responde à conversa com os pedaços dados (streaming) e à checagem de compromisso com `extraction`."""
    name = "talker"
    model = "talk-1"

    def __init__(self, pieces, extraction='{"commitment": null}'):
        self.pieces = list(pieces)
        self.extraction = extraction
        self.json_calls = 0

    def chat(self, messages, *, temperature=None, json_mode=False):
        if json_mode:
            self.json_calls += 1
            return LLMResponse(self.extraction, self.model)
        return LLMResponse("".join(self.pieces), self.model)

    def stream_chat(self, messages, *, temperature=None):
        for p in self.pieces:
            yield StreamEvent(text=p)
        yield StreamEvent(done=True, stats={})

    def check_availability(self):
        return ProviderStatus(True, "ok")


class TagTests(unittest.TestCase):
    def test_tag_is_hidden_even_halfway(self):
        self.assertEqual(commitments.visible_text("Deixa comigo. [[faz"), "Deixa comigo. ")
        self.assertEqual(commitments.visible_text("Deixa comigo. ["), "Deixa comigo. ")
        self.assertEqual(commitments.visible_text("Deixa comigo.\n[[fazer: pesquisar editores]]", final=True), "Deixa comigo.")
        self.assertEqual(commitments.extract_tags("ok [[ FAZER : pesquisar  editores de vídeo ]]"), ["pesquisar editores de vídeo"])

    def test_promise_detection(self):
        for text in ("Vou pesquisar isso e te falo.", "Deixa comigo, já vou anotar.", "Pode deixar que eu procuro uns tutoriais."):
            self.assertTrue(commitments.sounds_like_promise(text), text)
        for text in ("Que legal, conta mais!", "Eu acho que Gabumon combina com você."):
            self.assertFalse(commitments.sounds_like_promise(text), text)


class ChatCommitmentTests(DbTestCase):
    def services(self, provider, **overrides):
        settings = self.make_settings(**overrides) if overrides else self.settings
        tasks = TaskManager(settings, ScriptedProvider([]), ToolRegistry())
        return ChatService(settings, provider, tasks=tasks), tasks

    def test_tag_starts_a_goal_right_away_and_never_shows(self):
        provider = Talker(["Bora! Vou ", "procurar uns editores bons.", "\n[[fa", "zer: pesquisar editores de vídeo gratuitos ", "e anotar os melhores]]"])
        chat, tasks = self.services(provider)
        events = list(chat.stream_message("pesquisa uns editores de vídeo gratuitos pra mim?"))
        shown = "".join(e["text"] for e in events if e["type"] == "delta")
        self.assertNotIn("[[", shown)
        self.assertNotIn("fazer:", shown)
        done = events[-1]
        self.assertEqual(done["reply"], "Bora! Vou procurar uns editores bons.")
        self.assertEqual(len(done["actions"]), 1)
        goal = tasks.get_goal(done["actions"][0]["goal_id"])
        self.assertEqual((goal["status"], goal["plan_state"], goal["origin"]), ("active", "needs_plan", "nym"))
        self.assertEqual(goal["chat_message_id"], done["assistant_message_id"])
        self.assertIn("pesquisa uns editores", goal["motivation"])
        self.assertEqual(chat.get_history()["messages"][-1]["content"], "Bora! Vou procurar uns editores bons.")

    def test_same_commitment_twice_does_not_duplicate(self):
        provider = Talker(["Tá. [[fazer: pesquisar editores de vídeo gratuitos]]"])
        chat, tasks = self.services(provider)
        list(chat.stream_message("pesquisa editores?"))
        events = list(chat.stream_message("e aí, vai pesquisar?"))
        self.assertEqual(events[-1]["actions"], [])
        self.assertEqual(len(rows(self.settings, "SELECT id FROM goals")), 1)

    def test_promise_without_tag_is_caught_in_the_background(self):
        provider = Talker(["Vou pesquisar isso agora e te conto."],
                          extraction=json.dumps({"commitment": "Pesquisar programas de edição de vídeo gratuitos"}))
        chat, tasks = self.services(provider)
        list(chat.stream_message("tem algum editor de vídeo gratuito bom?"))
        for _ in range(50):
            if rows(self.settings, "SELECT id FROM goals"):
                break
            time.sleep(0.05)
        goals = rows(self.settings, "SELECT description, status FROM goals")
        self.assertEqual(goals, [{"description": "Pesquisar programas de edição de vídeo gratuitos", "status": "active"}])

    def test_plain_chat_creates_nothing_and_costs_no_extra_call(self):
        provider = Talker(["Que legal! Conta mais."])
        chat, _ = self.services(provider)
        list(chat.stream_message("vi um filme hoje"))
        time.sleep(0.1)
        self.assertEqual(provider.json_calls, 0)
        self.assertEqual(rows(self.settings, "SELECT id FROM goals"), [])

    def test_non_streaming_path_also_acts(self):
        provider = Talker(["Pode deixar. [[fazer: anotar as ideias de vídeo que ele contou]]"])
        chat, _ = self.services(provider)
        result = chat.send_message("anota minhas ideias de vídeo?")
        self.assertEqual(result.reply, "Pode deixar.")
        self.assertEqual(len(result.actions), 1)

    def test_can_be_turned_off(self):
        provider = Talker(["Tá. [[fazer: pesquisar editores de vídeo]]"])
        chat, _ = self.services(provider, chat_commitments=False)
        events = list(chat.stream_message("pesquisa?"))
        self.assertEqual(events[-1]["reply"], "Tá.")             # a marca some do texto mesmo assim
        self.assertEqual(rows(self.settings, "SELECT id FROM goals"), [])


if __name__ == "__main__":
    unittest.main()
