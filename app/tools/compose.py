"""compose_note: o Nym escreve (ou atualiza) uma nota a partir do trabalho REAL do objetivo (Fase 6.1).

Por que existe: os passos de um plano são decididos antes de rodar. Com `write_note`, o texto da nota era escrito no
planejamento, antes da pesquisa, e saía como intenção ("conteúdo a ser preenchido..."). Aqui o texto é escrito na hora
de executar, pelo modelo, usando só os resultados dos passos que já terminaram (e a versão anterior da nota, se ela
existir). Sem material real, a nota não é escrita.
"""
from __future__ import annotations

from typing import Any

from app.database.connection import connect
from app.models.base import ChatMessage, ModelError
from app.tools.base import Param, Tool, ToolContext, ToolError
from app.tools.builtin import MAX_NOTE_CHARS, _note_path, looks_unfinished, write_note_file

MAX_MATERIAL_CHARS = 9000
NO_CONTENT = "SEM_CONTEUDO"

PROMPT = """Você é o Nym escrevendo uma nota sua, em português, em Markdown simples (parágrafos e listas curtas).
Use SOMENTE o material abaixo (resultados reais do seu trabalho e a versão anterior da nota, se houver).
- Nada de placeholders, colchetes para preencher ou promessas ("a ser preenchido", "[inserir]"): escreva o conteúdo de fato.
- Cite de onde veio quando o material trouxer fonte (URL ou id de lembrança).
- Se for atualização, mantenha o que continua valendo e acrescente o que é novo.
- Não escreva o título (ele é colocado automaticamente).
- Se o material não tiver nada útil para a nota, responda exatamente: SEM_CONTEUDO"""


def _material(ctx: ToolContext) -> tuple[str, str]:
    if ctx.goal_id is None:
        raise ToolError("compose_note só roda dentro de um objetivo (precisa dos resultados dos passos anteriores).")
    conn = connect(ctx.settings.db_path)
    try:
        goal = conn.execute("SELECT description, completion_criteria FROM goals WHERE id = ?", (ctx.goal_id,)).fetchone()
        rows = conn.execute("SELECT step, tool, title, result FROM tasks WHERE goal_id = ? AND status = 'done' "
                            "AND tool != 'compose_note' ORDER BY step, id", (ctx.goal_id,)).fetchall()
    finally:
        conn.close()
    parts = [f"- Passo {r['step']} ({r['tool']}: {r['title']}):\n{(r['result'] or '').strip()}" for r in rows if (r["result"] or "").strip()]
    goal_text = goal["description"] + (f" (termina quando: {goal['completion_criteria']})" if goal and goal["completion_criteria"] else "")
    return goal_text, "\n\n".join(parts)[:MAX_MATERIAL_CHARS]


def _compose_note(ctx: ToolContext, args: dict[str, Any]) -> str:
    if ctx.provider is None:
        raise ToolError("O modelo não está disponível para escrever a nota.", retryable=True)
    title = args["title"]
    if looks_unfinished(title):
        raise ToolError("O título tem marcador para preencher; use um título de verdade.")
    path = _note_path(ctx, title)
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    goal_text, material = _material(ctx)
    if not material and not previous:
        raise ToolError("Ainda não há resultado de nenhum passo para escrever a nota. Faça a busca/leitura antes.")
    user = (f"Objetivo: {goal_text}\nO que a nota deve conter: {args['instructions']}\n\n"
            f"Resultados do seu trabalho:\n{material or '(nenhum novo)'}\n\n"
            + (f"Versão anterior da nota:\n{previous[:4000]}" if previous else "A nota ainda não existe."))
    try:
        reply = ctx.provider.chat([ChatMessage("system", PROMPT), ChatMessage("user", user)], temperature=0.4)
    except ModelError as exc:
        raise ToolError(f"O modelo não respondeu: {exc}", retryable=True) from None
    text = reply.content.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("markdown").strip()
    if not text or text.startswith(NO_CONTENT):
        raise ToolError("Não havia material de verdade para a nota; nada foi escrito.")
    if looks_unfinished(text):
        raise ToolError("O texto saiu com partes para preencher; nada foi escrito.", retryable=True)
    return write_note_file(ctx, title, text[:MAX_NOTE_CHARS - 200], replace=True)


COMPOSE_TOOLS: tuple[Tool, ...] = (
    Tool("compose_note", "Escreve ou atualiza uma nota sua com o que você descobriu nos passos ANTERIORES deste objetivo "
                         "(o texto é escrito na hora, a partir dos resultados reais).",
         {"title": Param(str, "título da nota (use o mesmo título para atualizar uma nota existente)", max_chars=120),
          "instructions": Param(str, "o que a nota deve conter", max_chars=400)},
         "medium", _compose_note, limits="só com resultados reais; nunca deixa placeholder; fica em data/notes/",
         errors="sem material, modelo indisponível", returns="caminho da nota",
         extra={"uses_model": True, "timeout": 240}),
)
