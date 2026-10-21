"""Ferramentas de baixo risco da Fase 5. Nenhuma acessa a rede nem arquivos fora de `data/notes/`.

- datetime_now   data e hora atuais (fuso do computador)
- calculate      aritmética segura (sem eval: árvore sintática com operadores permitidos)
- memory_search  consulta as lembranças do Nym (somente leitura)
- list_notes / read_note / write_note   notas do próprio Nym em data/notes/ (escrever pede permissão por padrão)

Pesquisa na internet e pastas autorizadas do usuário ficam para a Fase 6.
"""
from __future__ import annotations

import ast
import math
import operator
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.timesense import describe_now
from app.tools.base import Param, Tool, ToolContext, ToolError

# --- data e hora ------------------------------------------------------------------------------------------


def _now(ctx: ToolContext, args: dict[str, Any]) -> str:
    now = ctx.clock() if ctx.clock else datetime.now(timezone.utc)
    return describe_now(now.astimezone(ctx.tz))


# --- cálculo --------------------------------------------------------------------------------------------------
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"sqrt": math.sqrt, "round": round, "abs": abs}
MAX_EXPONENT = 100
MAX_NODES = 60


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > MAX_EXPONENT or abs(left) > 10 ** 6):
            raise ToolError("Potência grande demais.")
        value = _BIN[type(node.op)](left, right)
        if isinstance(value, int) and abs(value) > 10 ** 300:
            raise ToolError("Resultado grande demais.")
        return value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
        return _UN[type(node.op)](_eval(node.operand))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS
            and not node.keywords and 1 <= len(node.args) <= 2):
        return _FUNCS[node.func.id](*(_eval(a) for a in node.args))
    raise ToolError("Expressão não permitida: use só números, + - * / // % **, parênteses, sqrt, round e abs.")


def _calculate(ctx: ToolContext, args: dict[str, Any]) -> str:
    expr = args["expression"].replace(",", ".").replace("x", "*").replace("^", "**")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise ToolError("Expressão inválida.") from None
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ToolError("Expressão longa demais.")
    try:
        value = _eval(tree.body)
    except ZeroDivisionError:
        raise ToolError("Divisão por zero.") from None
    except (OverflowError, ValueError) as exc:
        raise ToolError(f"Não deu para calcular: {exc}") from None
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    return f"{args['expression']} = {value}"


# --- memória ---------------------------------------------------------------------------------------------------


def _memory_search(ctx: ToolContext, args: dict[str, Any]) -> str:
    if ctx.memory is None:
        raise ToolError("A memória não está disponível.")
    hits = ctx.memory.retrieve(args["query"], int(args.get("limit") or 5))
    if not hits:
        return "Nenhuma lembrança encontrada."
    lines = []
    for h in hits:
        m = h["memory"]
        certainty = m.get("epistemic") or "episódio"
        lines.append(f"#{m['id']} ({certainty}): {m['content']}")
    return "\n".join(lines)


# --- notas -------------------------------------------------------------------------------------------------------
MAX_NOTE_CHARS = 20_000
MAX_NOTES = 500


def _notes_dir(ctx: ToolContext) -> Path:
    path = Path(ctx.settings.data_dir) / "notes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def note_filename(title: str) -> str:
    """Nome de arquivo seguro: sem acentos, sem barras, sem '..'. Nunca sai de data/notes/."""
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:60]
    if not slug:
        raise ToolError("Título inválido para nota.")
    return slug + ".md"


def _note_path(ctx: ToolContext, title: str) -> Path:
    base = _notes_dir(ctx).resolve()
    path = (base / note_filename(title)).resolve()
    if path.parent != base:  # defesa extra: o nome já é limpo, mas nunca escreve fora da pasta
        raise ToolError("Caminho de nota inválido.")
    return path


def _list_notes(ctx: ToolContext, args: dict[str, Any]) -> str:
    files = sorted(p.name for p in _notes_dir(ctx).glob("*.md"))
    return "\n".join(files) if files else "Nenhuma nota ainda."


def _read_note(ctx: ToolContext, args: dict[str, Any]) -> str:
    path = _note_path(ctx, args["title"])
    if not path.is_file():
        # Não é falha: o resultado diz o que existe, e o próximo passo usa um título real (Fase 8.2).
        folder = path.parent
        titles = sorted(p.stem for p in folder.glob("*.md")) if folder.is_dir() else []
        return (f"Ainda não existe nota com esse título ({path.name}). "
                + (f"Notas que existem: {', '.join(titles[:40])}." if titles else "Você ainda não tem nenhuma nota.")
                + " Para criar, use compose_note ou write_note.")
    return path.read_text(encoding="utf-8")[:MAX_NOTE_CHARS]


_UNFINISHED = re.compile(
    r"(\[\s*(inserir|preencher|adicionar|incluir|data e hora|data|exemplos?|conte[uú]do|a ser|todo)[^\]]*\]"
    r"|a ser preenchid[oa]|conte[uú]do a ser|ser[aá] preenchid[oa]|preencher depois|\bTODO\b|\bTBD\b|lorem ipsum)",
    re.I)


def looks_unfinished(text: str) -> bool:
    """Texto com marcador para preencher depois: não é conteúdo de verdade."""
    return bool(_UNFINISHED.search(text or ""))


def write_note_file(ctx: ToolContext, title: str, content: str, *, append: bool = False, replace: bool = False) -> str:
    path = _note_path(ctx, title)
    if not path.exists() and len(list(_notes_dir(ctx).glob("*.md"))) >= MAX_NOTES:
        raise ToolError(f"Limite de {MAX_NOTES} notas atingido.")
    if path.exists() and not (append or replace):
        raise ToolError(f"A nota {path.name} já existe; use append para acrescentar ou compose_note para atualizar.")
    old = path.read_text(encoding="utf-8") if path.exists() and append else ""
    new = (old + "\n\n" if old else f"# {title}\n\n") + content.strip() + "\n"
    if len(new) > MAX_NOTE_CHARS:
        raise ToolError(f"A nota passaria de {MAX_NOTE_CHARS} caracteres.")
    existed = path.exists()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(new, encoding="utf-8")
    tmp.replace(path)  # gravação atômica
    verb = "Acrescentado em" if append and existed else "Nota atualizada:" if existed else "Nota criada:"
    return f"{verb} data/notes/{path.name}"


def _write_note(ctx: ToolContext, args: dict[str, Any]) -> str:
    if looks_unfinished(args["title"]) or looks_unfinished(args["content"]):
        raise ToolError("Isso é um rascunho com partes para preencher, não uma nota. Para escrever com o resultado do "
                        "trabalho, use compose_note depois dos passos de busca.")
    return write_note_file(ctx, args["title"], args["content"], append=bool(args.get("append")))


BUILTIN_TOOLS: tuple[Tool, ...] = (
    Tool("datetime_now", "Diz a data e a hora atuais.", {}, "low", _now,
         returns="texto com dia da semana, data e hora"),
    Tool("calculate", "Faz uma conta.", {"expression": Param(str, "expressão, ex.: (12*3)/4", max_chars=200)}, "low",
         _calculate, limits="só aritmética; expoente até 100", errors="expressão inválida ou não permitida",
         returns="a conta e o resultado"),
    Tool("memory_search", "Procura nas suas lembranças.",
         {"query": Param(str, "o que procurar", max_chars=300),
          "limit": Param(int, "quantas lembranças (1 a 10)", required=False, minimum=1, maximum=10)},
         "low", _memory_search, limits="somente leitura", returns="lembranças com id e grau de certeza"),
    Tool("list_notes", "Lista as suas notas.", {}, "low", _list_notes, returns="nomes das notas"),
    Tool("read_note", "Lê uma nota sua.", {"title": Param(str, "título da nota", max_chars=120)}, "low", _read_note,
         errors="nota inexistente", returns="conteúdo da nota"),
    Tool("write_note", "Escreve uma nota curta com um texto que você JÁ tem agora (para texto que depende de "
                       "pesquisa ou leitura, use compose_note).",
         {"title": Param(str, "título", max_chars=120), "content": Param(str, "texto", max_chars=5000),
          "append": Param(bool, "acrescentar a uma nota existente", required=False)},
         "medium", _write_note, limits=f"só dentro de data/notes/; até {MAX_NOTES} notas de {MAX_NOTE_CHARS} caracteres",
         errors="nota já existe (sem append), limite atingido", returns="caminho da nota"),
)
