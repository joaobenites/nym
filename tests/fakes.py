"""Dublês de teste. Existem apenas em tests/: não fazem parte do produto."""
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.database.migrator import initialize_database
from app.logging_setup import shutdown_logging
from app.models.base import LLMProvider, LLMResponse, ProviderStatus

CONTEXT_CLOSE = "[Fim do contexto. A mensagem dele vem a seguir.]"


def spoken(text):
    """A fala do usuário sem o bloco de contexto que o chat anexa à última mensagem."""
    return text.split(CONTEXT_CLOSE, 1)[1].lstrip("\n") if CONTEXT_CLOSE in text else text


def prompt_of(call):
    """Todo o texto enviado ao modelo numa chamada (sistema + histórico + contexto do momento)."""
    return "\n\n".join(m.content for m in call)


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, model="fake-1", fail_with=None):
        self.model = model
        self.fail_with = fail_with
        self.calls = []

    def chat(self, messages, *, temperature=None, json_mode=False):
        self.calls.append(list(messages))
        self.last_kwargs = {"temperature": temperature, "json_mode": json_mode}
        if self.fail_with is not None:
            raise self.fail_with
        return LLMResponse(content=f"eco: {spoken(messages[-1].content)}", model=self.model)

    def check_availability(self):
        if self.fail_with is not None:
            return ProviderStatus(False, str(self.fail_with))
        return ProviderStatus(True, "fake ok")


class ScriptedProvider(LLMProvider):
    """Devolve respostas pré-definidas, em ordem (para testar a consolidação). Aceita exceções na lista."""
    name = "scripted"

    def __init__(self, replies, model="scripted-1"):
        self.model = model
        self.replies = list(replies)
        self.calls = []
        self.kwargs = []

    def chat(self, messages, *, temperature=None, json_mode=False):
        self.calls.append(list(messages))
        self.kwargs.append({"temperature": temperature, "json_mode": json_mode})
        reply = self.replies.pop(0) if self.replies else "{}"
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(content=reply, model=self.model)

    def check_availability(self):
        return ProviderStatus(True, "scripted ok")


class DbTestCase(unittest.TestCase):
    """Cria um diretório de dados temporário com o banco já migrado."""

    def make_settings(self, **overrides):
        return Settings(data_dir=Path(self.tmp.name), **overrides)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Registrado depois, roda antes: fecha o nym.log para a pasta poder ser apagada (Windows).
        self.addCleanup(shutdown_logging)
        self.settings = self.make_settings()
        initialize_database(self.settings.db_path)
