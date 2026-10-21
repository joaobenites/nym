"""Teste de cérebro (app/brain_check.py): roda as tarefas reais numa cópia e nunca toca no banco original."""
import json
from unittest import mock

from app import brain_check
from app.models.base import LLMProvider, LLMResponse, ProviderStatus
from tests.fakes import DbTestCase
from tests.memtools import seed_chat


class RoleProvider(LLMProvider):
    """Responde conforme o tipo de pedido, como um modelo comportado faria."""
    name = "fake"

    def __init__(self, *args, **kwargs):
        self.model = args[1] if len(args) > 1 else "fake"

    def chat(self, messages, *, temperature=None, json_mode=False):
        system = messages[0].content
        if json_mode and '"goals"' in system:
            return LLMResponse(content='<think>hmm</think>{"goals": []}', model=self.model)
        if json_mode:
            return LLMResponse(content=json.dumps({"steps": [{"title": "Buscar", "tool": "web_search",
                                                              "args": {"query": "Digimon Tamers"}}]}), model=self.model)
        if "diário" in system:
            return LLMResponse(content="Hoje conversei bastante com o João sobre desenhos e fiquei pensando nisso.\n"
                                       "Amanhã: desenhar de novo.", model=self.model)
        return LLMResponse(content="Opa, tudo certo por aqui.", model=self.model)

    def check_availability(self):
        return ProviderStatus(True, "ok")


class BrainCheckTests(DbTestCase):
    def test_runs_real_tasks_on_a_copy_and_reports(self):
        seed_chat(self.settings, [("user", "oi", -60 * 24), ("assistant", "oi!", -60 * 24 + 1)])
        before = self.settings.db_path.read_bytes()
        with mock.patch.object(brain_check, "OllamaProvider", RoleProvider):
            r = brain_check.check_model(self.settings, "modelo-x")
        self.assertEqual(self.settings.db_path.read_bytes(), before)        # o banco de verdade não mudou
        names = [x[0] for x in r["results"]]
        self.assertEqual(names[:3], ["conversa"] * 3)
        self.assertIn("vontades (JSON)", names)
        self.assertIn("plano (JSON)", names)
        self.assertTrue(all(x[2] for x in r["results"]), r["results"])
        text = brain_check.report([r])
        self.assertIn("modelo-x", text)

    def test_unavailable_model_is_reported_not_crashed(self):
        class Missing(RoleProvider):
            def check_availability(self):
                return ProviderStatus(False, "modelo não encontrado")
        with mock.patch.object(brain_check, "OllamaProvider", Missing):
            r = brain_check.check_model(self.settings, "nao-existe")
        self.assertFalse(r["available"])
        self.assertIn("ollama pull nao-existe", brain_check.report([r]))
        r["detail"] = "Não foi possível conectar ao Ollama"
        self.assertIn("abra o Ollama", brain_check.report([r]))
