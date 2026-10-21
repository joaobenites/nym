"""Abstração do modelo de linguagem.

Os demais subsistemas dependem apenas de `LLMProvider`; trocar de modelo ou de
provedor não exige alterar o restante do sistema nem o banco de dados.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator


class ModelError(Exception):
    """Falha ao usar o modelo (resposta inválida, erro do servidor, etc.)."""


class ModelUnavailableError(ModelError):
    """O modelo não pôde ser alcançado (servidor fora do ar, modelo ausente, timeout)."""


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str
    images: tuple[str, ...] = ()   # imagens em base64 (só para modelos com visão; Fase 8 - sentidos)


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    stats: dict[str, Any] = field(default_factory=dict)   # tempos e contagens, quando o provedor informa


@dataclass(frozen=True)
class StreamEvent:
    """Pedaço de uma resposta em streaming. `done=True` vem uma vez, no fim, com as estatísticas."""
    text: str = ""
    done: bool = False
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderStatus:
    available: bool
    detail: str


class LLMProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def chat(
        self, messages: list[ChatMessage], *, temperature: float | None = None, json_mode: bool = False
    ) -> LLMResponse:
        """Envia a conversa ao modelo. Levanta ModelUnavailableError ou ModelError.

        `temperature` substitui o padrão só nesta chamada; `json_mode` pede saída em JSON."""

    def stream_chat(self, messages: list[ChatMessage], *, temperature: float | None = None) -> Iterator[StreamEvent]:
        """Resposta em pedaços, conforme é gerada. Padrão: uma resposta só (provedores sem streaming)."""
        response = self.chat(messages, temperature=temperature)
        yield StreamEvent(text=response.content)
        yield StreamEvent(done=True, stats=response.stats)

    def supports_vision(self) -> bool:
        """O modelo enxerga imagens? Padrão: não (provedores que sabem perguntar ao servidor sobrescrevem)."""
        return False

    def warm_up(self) -> None:
        """Carrega o modelo antes da primeira mensagem, se o provedor souber fazer isso. Nunca levanta exceção."""

    @abstractmethod
    def check_availability(self) -> ProviderStatus:
        """Verifica se o modelo está utilizável. Nunca levanta exceção."""
