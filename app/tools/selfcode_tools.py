"""Ferramentas de auto-melhoria (Fase 7): ler o próprio código e propor mudanças. Aplicar NÃO é ferramenta: só
o usuário aplica, pela página Ateliê. Regras e validações em `app/creations/selfcode.py`."""
from __future__ import annotations

from typing import Any

from app.creations import selfcode
from app.database.connection import connect, transaction
from app.database.repository import utcnow
from app.models.base import ChatMessage, ModelError
from app.tools.base import Param, Tool, ToolContext, ToolError

PROMPT = """Você é o Nym propondo uma melhoria no SEU próprio código. Quem decide se aplica é o usuário, depois de
ler a proposta. Responda SOMENTE com JSON:
{"why": "o que melhora e por quê, em 1 a 3 frases, em primeira pessoa",
 "edits": [{"find": "trecho EXATO copiado do arquivo (sem os números de linha), com a indentação", "replace": "como fica"}]}
Regras:
- Mudanças pequenas e focadas (no máximo 5 trocas). Cada "find" precisa aparecer exatamente uma vez no arquivo:
  copie linhas inteiras e inclua linhas vizinhas se precisar ficar único.
- Mantenha o estilo do arquivo e o português nos textos.
- Não mexa em segurança, permissões, rede ou em como você é avaliado: esses arquivos são protegidos.
- Se não houver melhoria boa e segura para fazer, responda {"why": "motivo", "edits": []}."""


def _root(ctx: ToolContext):
    if not getattr(ctx.settings, "self_code", True):
        raise ToolError("Auto-melhoria desligada (NYM_SELF_CODE=false).")
    return ctx.settings.project_root


def _read(ctx: ToolContext, args: dict[str, Any]) -> str:
    try:
        return selfcode.read_code(_root(ctx), args.get("path") or "app", args.get("focus"))
    except selfcode.SelfCodeError as exc:
        raise ToolError(str(exc)) from None


def _propose(ctx: ToolContext, args: dict[str, Any]) -> str:
    from app.tasks.planner import PlanError, load_json  # import local: o planejador importa o registro
    root = _root(ctx)
    if ctx.provider is None:
        raise ToolError("O modelo não está disponível.", retryable=True)
    try:
        view = selfcode.read_code(root, args["path"], args.get("focus"))
    except selfcode.SelfCodeError as exc:
        raise ToolError(str(exc)) from None
    _full, rel = selfcode._rel(root, args["path"])
    if not selfcode.editable(rel):
        raise ToolError(f"{rel} é protegido: você pode ler, mas não propor mudança nele.")
    user = f"Arquivo: {rel}\nMelhoria que você quer: {args['instructions']}\n\nCódigo atual (números de linha só para referência):\n{view}"
    try:
        reply = ctx.provider.chat([ChatMessage("system", PROMPT), ChatMessage("user", user)], temperature=0.2, json_mode=True)
        data = load_json(reply.content)
    except ModelError as exc:
        raise ToolError(f"O modelo não respondeu: {exc}", retryable=True) from None
    except PlanError as exc:
        raise ToolError(str(exc), retryable=True) from None
    why = " ".join(str(data.get("why") or "").split())[:600]
    edits = data.get("edits")
    if not edits:
        raise ToolError(f"Não propus mudança: {why or 'não achei melhoria segura'}.")
    try:
        p = selfcode.build(root, rel, edits)
        conn = connect(ctx.settings.db_path)
        try:
            with transaction(conn):
                pid = selfcode.store(conn, p, args["title"], why or args["instructions"], ctx.goal_id,
                                     ctx.clock().isoformat(timespec="milliseconds") if ctx.clock else utcnow())
        finally:
            conn.close()
    except selfcode.SelfCodeError as exc:
        raise ToolError(f"Proposta recusada pela checagem: {exc}", retryable=True) from None
    warn = f" Avisos: {'; '.join(p.warnings)}." if p.warnings else ""
    return (f"Proposta de melhoria #{pid} guardada para ele revisar no Ateliê ({rel}, {len(p.edits)} troca(s)). "
            f"Ainda NÃO foi aplicada: só ele aplica.{warn}")


SELFCODE_TOOLS: tuple[Tool, ...] = (
    Tool("read_own_code", "Lê o seu próprio código (pastas app/ e frontend/, README, CLAUDE.md): sem path lista app/; "
                          "pasta lista arquivos; arquivo mostra o conteúdo numerado. focus mostra a região de um trecho.",
         {"path": Param(str, "pasta ou arquivo, ex.: app/core/voice.py", required=False, max_chars=200),
          "focus": Param(str, "trecho para localizar dentro do arquivo", required=False, max_chars=120)},
         "low", _read, limits="só leitura; só a pasta do projeto; nada de .env nem dados", errors="caminho inválido",
         returns="lista de arquivos ou código numerado"),
    Tool("propose_improvement", "Propõe uma melhoria no seu próprio código (um arquivo por vez). Vira uma proposta com "
                                "diff para ele revisar e aplicar no Ateliê; você nunca aplica sozinho.",
         {"path": Param(str, "arquivo a melhorar (app/... ou frontend/...)", max_chars=200),
          "title": Param(str, "nome curto da melhoria", max_chars=120),
          "instructions": Param(str, "o que melhorar e por quê", max_chars=800),
          "focus": Param(str, "trecho do arquivo onde está a parte a mudar (para arquivos grandes)", required=False,
                         max_chars=120)},
         "low", _propose,
         limits="arquivos de segurança/permissões/rede/banco/API protegidos; trocas exatas; Python válido; "
                "sem comandos do sistema nem execução dinâmica; só o usuário aplica",
         errors="trecho não encontrado, arquivo protegido, mudança recusada", returns="id da proposta",
         extra={"uses_model": True, "timeout": 300}),
)
