"""Contrato de uma ferramenta do Nym.

Cada ferramenta declara nome, descrição, parâmetros (com validação), risco e limites. O código valida os argumentos
ANTES de executar; o modelo só sugere. Nenhuma ferramenta tem acesso irrestrito ao sistema: cada uma faz uma coisa,
dentro de limites fixos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

RISKS = ("low", "medium", "high")


class ToolError(Exception):
    """Falha da ferramenta. `retryable` indica se tentar de novo pode adiantar."""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class InvalidToolArgs(ToolError):
    pass


@dataclass(frozen=True)
class Param:
    type: type                    # str, int, float ou bool
    description: str
    required: bool = True
    max_chars: int = 500          # só para str
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class ToolContext:
    """O que a ferramenta pode usar. Passado pelo executor; nada além disso."""
    settings: Any
    memory: Any = None
    clock: Callable[[], Any] | None = None
    tz: Any = None
    goal_id: int | None = None    # para registrar pesquisas e conferir fontes do mesmo objetivo
    task_id: int | None = None
    http: Any = None              # cliente HTTP (troca-se nos testes); None = o padrão de app/tools/web.py
    provider: Any = None          # modelo, para ferramentas que escrevem texto (compose_note)
    senses: Any = None            # VisionService (Fase 8), para ferramentas que olham imagens e vídeos


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: Mapping[str, Param]
    risk: str
    run: Callable[[ToolContext, dict[str, Any]], str]
    limits: str = ""
    errors: str = ""
    returns: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self, args: Any) -> dict[str, Any]:
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise InvalidToolArgs("Os argumentos devem ser um objeto.")
        unknown = set(args) - set(self.params)
        if unknown:
            raise InvalidToolArgs(f"Parâmetros desconhecidos: {sorted(unknown)}.")
        clean: dict[str, Any] = {}
        for name, p in self.params.items():
            if name not in args or args[name] is None:
                if p.required:
                    raise InvalidToolArgs(f"Falta o parâmetro '{name}'.")
                continue
            value = args[name]
            if p.type is float and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            if p.type is not bool and isinstance(value, bool) or not isinstance(value, p.type):
                raise InvalidToolArgs(f"'{name}' deve ser do tipo {p.type.__name__}.")
            if p.type is str:
                value = value.strip()
                if p.required and not value:
                    raise InvalidToolArgs(f"'{name}' não pode ser vazio.")
                if len(value) > p.max_chars:
                    raise InvalidToolArgs(f"'{name}' excede {p.max_chars} caracteres.")
            if p.type in (int, float):
                if p.minimum is not None and value < p.minimum or p.maximum is not None and value > p.maximum:
                    raise InvalidToolArgs(f"'{name}' fora do intervalo permitido.")
            clean[name] = value
        return clean

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name, "description": self.description, "risk": self.risk, "limits": self.limits,
            "errors": self.errors, "returns": self.returns,
            "params": {k: {"type": p.type.__name__, "description": p.description, "required": p.required}
                       for k, p in self.params.items()},
        }
