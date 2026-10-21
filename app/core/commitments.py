"""Compromissos feitos na conversa (Fase 6.1): quando o Nym diz que vai fazer algo, o trabalho começa de verdade.

Dois caminhos:
1. Marca explícita: o modelo termina a resposta com uma linha `[[fazer: ...]]`. A linha nunca aparece para o usuário
   (é escondida também durante o streaming) e vira um objetivo ativo na hora.
2. Promessa sem marca ("vou pesquisar isso", "deixa comigo"): uma checagem curta em segundo plano pergunta ao modelo
   se houve compromisso concreto e, se houve, cria o objetivo do mesmo jeito.

O texto gerado não é tratado como ação feita: ele só REGISTRA o objetivo. A execução continua no gerenciador de
tarefas, com plano validado, ferramentas e permissões.
"""
from __future__ import annotations

import re
from typing import Any

from app.memory.text import normalize
from app.models.base import ChatMessage

TAG_RE = re.compile(r"\[\[\s*fazer\s*:\s*(.+?)\s*\]\]", re.I | re.S)
MAX_TAGS = 2

# Promessas típicas em português ("vou pesquisar", "já vou ver", "deixa comigo", "vou dar uma olhada"...).
PROMISE_RE = re.compile(
    r"\b(vou|irei|ja vou|deixa que eu|deixa comigo|pode deixar que eu|vou tentar)\b.{0,25}\b"
    r"(pesquis|procur|busc|anot|escrev|organiz|junt|ler|le[ir]|olhar|olhada|ver isso|investig|descobr|lista|resum|calcul|"
    r"comparar|separ|montar|fazer isso|cuidar)", re.I)
ASKS_FOR_ACTION_RE = re.compile(r"\b(pesquis|procur|busc|anot|organiz|lista|resum|descobr|le[ir]a?|ve[r]?\s+pra mim|faz|faca)", re.I)

EXTRACT_PROMPT = """Você lê a última troca de mensagens entre o usuário e o Nym e responde SOMENTE com JSON:
{"commitment": null}  ou  {"commitment": "o que o Nym se comprometeu a fazer, em uma frase, como objetivo"}
Só há compromisso se o Nym disse claramente que VAI fazer algo concreto agora ou em seguida (pesquisar, anotar,
organizar, ler, juntar informações...). Concordar, opinar ou só conversar não é compromisso."""


def visible_text(raw: str, final: bool = False) -> str:
    """O texto que o usuário pode ver: sem as marcas completas e, durante o streaming, sem uma marca pela metade."""
    out = TAG_RE.sub("", raw)
    if not final:
        start = out.find("[[")
        if start >= 0:
            out = out[:start]
        elif out.endswith("["):
            out = out[:-1]
    return out.rstrip() if final else out


def extract_tags(raw: str) -> list[str]:
    found = []
    for m in TAG_RE.finditer(raw):
        text = " ".join(m.group(1).split())
        if text and text not in found:
            found.append(text)
    return found[:MAX_TAGS]


def sounds_like_promise(reply: str) -> bool:
    return bool(PROMISE_RE.search(normalize(reply)))


def extraction_messages(user_text: str, reply: str) -> list[ChatMessage]:
    return [ChatMessage("system", EXTRACT_PROMPT),
            ChatMessage("user", f"Usuário: {user_text[:800]}\nNym: {reply[:800]}")]


def parse_extraction(raw: str) -> str | None:
    from app.tasks.planner import PlanError, load_json  # import local: evita ciclo
    try:
        data: Any = load_json(raw)
    except PlanError:
        return None
    value = data.get("commitment")
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value if len(value) >= 8 else None
