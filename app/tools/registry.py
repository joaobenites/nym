"""Registro de ferramentas e permissões.

Política (fixa no código, não editável por conversa, memória ou pelo próprio Nym):
- Sem escolha do usuário, vale o padrão pelo risco: baixo = automático, médio = perguntar, alto = negado.
- O usuário pode mudar o modo de cada ferramenta (auto / perguntar / negar), mas ferramenta de risco ALTO nunca
  fica automática. Só a API do usuário altera permissões; o Nym não tem caminho para isso.
- Revogar vale na hora: o executor confere a permissão de novo imediatamente antes de rodar.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from app.database.repository import log_audit, utcnow
from app.tools.base import Tool
from app.tools.builtin import BUILTIN_TOOLS
from app.tools.compose import COMPOSE_TOOLS
from app.tools.create import CREATE_TOOLS
from app.tools.folders import FOLDER_TOOLS
from app.tools.selfcode_tools import SELFCODE_TOOLS
from app.tools.senses_tools import SENSES_TOOLS
from app.tools.web import WEB_TOOLS

ALL_TOOLS = (*BUILTIN_TOOLS, *COMPOSE_TOOLS, *CREATE_TOOLS, *WEB_TOOLS, *FOLDER_TOOLS, *SELFCODE_TOOLS, *SENSES_TOOLS)

MODES = ("auto", "ask", "deny")
DEFAULT_MODE = {"low": "auto", "medium": "ask", "high": "deny"}


class PermissionInputError(ValueError):
    pass


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ALL_TOOLS) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"Ferramenta duplicada: {tool.name}")
            self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    # --- permissões ---------------------------------------------------------------------------------
    def mode(self, conn: sqlite3.Connection, name: str) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return "deny"
        row = conn.execute("SELECT mode FROM tool_permissions WHERE tool = ?", (name,)).fetchone()
        mode = row["mode"] if row else DEFAULT_MODE[tool.risk]
        if tool.risk == "high" and mode == "auto":
            mode = "ask"  # trava fixa: risco alto nunca roda sozinho
        return mode

    def set_mode(self, conn: sqlite3.Connection, name: str, mode: str) -> str:
        tool = self._tools.get(name)
        if tool is None:
            raise PermissionInputError("Ferramenta desconhecida.")
        if mode not in MODES:
            raise PermissionInputError("Modo inválido: use auto, ask ou deny.")
        if tool.risk == "high" and mode == "auto":
            raise PermissionInputError("Ferramentas de risco alto não podem rodar automaticamente.")
        conn.execute("INSERT INTO tool_permissions (tool, mode, updated_at) VALUES (?, ?, ?) "
                     "ON CONFLICT(tool) DO UPDATE SET mode = excluded.mode, updated_at = excluded.updated_at",
                     (name, mode, utcnow()))
        log_audit(conn, "tool.permission", {"tool": name, "mode": mode, "by": "user"})
        return mode

    def overview(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        return [{**t.describe(), "mode": self.mode(conn, t.name), "default_mode": DEFAULT_MODE[t.risk]} for t in self.all()]

    def prompt_catalog(self) -> str:
        """Descrição das ferramentas para o planejador (nome, parâmetros e para que serve)."""
        lines = []
        for t in self.all():
            params = ", ".join(f"{k}: {p.type.__name__}{'' if p.required else ' (opcional)'} - {p.description}"
                               for k, p in t.params.items()) or "sem parâmetros"
            lines.append(f"- {t.name}: {t.description} Parâmetros: {params}.")
        return "\n".join(lines)
