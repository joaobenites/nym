"""O que o modelo propõe num ciclo de sonho (revisão, associações, hipóteses, perguntas...) e sua validação.

O modelo PROPÕE; o código DECIDE. Tudo precisa citar ids de memórias que realmente foram selecionadas para o ciclo.
Itens sem esse lastro são descartados: um sonho nunca pode inventar o que não está nas memórias.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.memory.extraction import load_json, ExtractionError
from app.memory.text import looks_sensitive
from app.models.base import ChatMessage

MAX_ASSOCIATIONS = 5
MAX_HYPOTHESES = 4
MAX_QUESTIONS = 4
MAX_EXPLORATION = 3
MAX_TEXT = 300
MAX_CONTENT_PER_MEMORY = 400

SYSTEM_PROMPT = """Você é o processo de revisão de memória do Nym, chamado Somnium. Você recebe memórias REAIS, cada uma com um id,
e deve revisá-las, achar ligações entre elas e levantar hipóteses e perguntas. Responda SOMENTE com um objeto JSON:
{
  "review": "3 a 5 frases em português: o que aconteceu, o que mudou e o que ainda é incerto",
  "associations": [{"memories": [ids], "text": "o que liga estas memórias", "confidence": 0.0 a 1.0}],
  "hypotheses": [{"text": "hipótese sobre o usuário, escrita como hipótese", "evidence": [ids], "confidence": 0.0 a 1.0}],
  "open_questions": [{"text": "pergunta ainda sem resposta", "evidence": [ids]}],
  "exploration": [{"question": "o que valeria investigar", "why": "por quê", "evidence": [ids]}],
  "dream_note": {"text": "uma coisa curta e natural que o Nym poderia comentar com o usuário", "evidence": [ids]}
}
Regras:
- Use SOMENTE ids da lista. Toda associação precisa de pelo menos 2 ids; todo o resto, de pelo menos 1.
- Hipótese não é fato: escreva como possibilidade ("talvez...", "pode ser que...") e nunca como certeza.
- Não invente nada além do que as memórias sustentam. Nunca registre senhas, documentos ou números de cartão.
- Prefira poucos itens bons a muitos fracos. Listas podem ficar vazias. "dream_note" pode ser null.
- Não repita as perguntas que já estão em aberto.
- NÃO repita o que já foi sonhado (lista "Sonhos anteriores"). A revisão fala do que é NOVO (memórias marcadas "novo")
  ou de uma ligação que ainda não foi feita. Nada de resumir de novo a relação inteira.
- Varie: se os sonhos anteriores giraram em torno de um tema, procure outro nas memórias.
- "dream_note" fala de uma coisa CONCRETA das memórias (algo que ele pesquisou, criou, disse ou fez), em até duas frases, como
  um amigo comentaria. Nada de refletir sobre a relação, sobre "evoluirmos juntos" ou sobre ser mais que uma ferramenta, e
  nunca comece com "É fascinante como", "É curioso como" ou parecido. Sem coisa concreta nova: "dream_note": null."""


@dataclass
class Assoc:
    text: str
    ids: list[int]
    confidence: float


@dataclass
class Hyp:
    text: str
    ids: list[int]
    confidence: float


@dataclass
class Question:
    text: str
    ids: list[int]


@dataclass
class Explore:
    question: str
    why: str
    ids: list[int]


@dataclass
class DreamResult:
    review: str
    associations: list[Assoc] = field(default_factory=list)
    hypotheses: list[Hyp] = field(default_factory=list)
    open_questions: list[Question] = field(default_factory=list)
    exploration: list[Explore] = field(default_factory=list)
    dream_note: tuple[str, list[int]] | None = None


def build_messages(selected: Sequence[Mapping[str, Any]], open_questions: Sequence[str],
                   previous: Sequence[str] = ()) -> list[ChatMessage]:
    label = {"episodic": "episódio", "semantic": "fato", "identity": "observação"}
    epi = {"confirmed": "confirmado", "hypothesis": "hipótese", "unverified": "não verificado", "sourced": "de fonte externa"}
    lines = []
    for m in selected:
        tag = label[m["kind"]] + (f", {epi[m['epistemic']]}" if m.get("epistemic") else "") + (", novo" if m.get("is_new") else "")
        lines.append(f"[{m['id']}] ({tag}, {str(m['occurred_at'])[:10]}) {m['content'][:MAX_CONTENT_PER_MEMORY]}")
    parts = ["Memórias para revisar:\n" + "\n".join(lines)]
    if open_questions:
        parts.append("Perguntas já em aberto (não repita):\n" + "\n".join(f"- {q}" for q in open_questions))
    if previous:
        parts.append("Sonhos anteriores (não repita; busque algo diferente):\n" + "\n".join(f"- {p[:300]}" for p in previous))
    return [ChatMessage("system", SYSTEM_PROMPT), ChatMessage("user", "\n\n".join(parts))]


def _ids(value: Any, valid: set[int]) -> list[int]:
    if not isinstance(value, list):
        return []
    return sorted({v for v in value if isinstance(v, int) and not isinstance(v, bool) and v in valid})


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())[:MAX_TEXT]
    return "" if looks_sensitive(text) else text


def _conf(value: Any, default: float, cap: float = 1.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return min(default, cap)
    return min(cap, max(0.0, float(value)))


def parse_dream(raw: str, valid_ids: set[int]) -> DreamResult:
    data = load_json(raw)
    review = data.get("review")
    if not isinstance(review, str) or not review.strip():
        raise ExtractionError("Revisão ausente na resposta do modelo.")
    result = DreamResult(review=" ".join(review.split())[:1200])

    def entries(key: str) -> list[dict[str, Any]]:
        raw_list = data.get(key)
        if raw_list is None:
            return []
        if not isinstance(raw_list, list):
            raise ExtractionError(f"Campo '{key}' não é uma lista.")
        return [e for e in raw_list if isinstance(e, dict)]

    for e in entries("associations"):
        ids, text = _ids(e.get("memories"), valid_ids), _text(e.get("text"))
        if len(ids) >= 2 and text:
            result.associations.append(Assoc(text, ids, _conf(e.get("confidence"), 0.5)))
    for e in entries("hypotheses"):
        ids, text = _ids(e.get("evidence"), valid_ids), _text(e.get("text"))
        if ids and text:
            result.hypotheses.append(Hyp(text, ids, _conf(e.get("confidence"), 0.4, cap=0.6)))  # hipótese nunca passa de 0,6
    for e in entries("open_questions"):
        ids, text = _ids(e.get("evidence"), valid_ids), _text(e.get("text"))
        if ids and text:
            result.open_questions.append(Question(text, ids))
    for e in entries("exploration"):
        ids, q = _ids(e.get("evidence"), valid_ids), _text(e.get("question"))
        if ids and q:
            result.exploration.append(Explore(q, _text(e.get("why")), ids))
    note = data.get("dream_note")
    if isinstance(note, dict):
        ids, text = _ids(note.get("evidence"), valid_ids), _text(note.get("text"))
        if ids and text:
            result.dream_note = (text, ids)

    result.associations = result.associations[:MAX_ASSOCIATIONS]
    result.hypotheses = result.hypotheses[:MAX_HYPOTHESES]
    result.open_questions = result.open_questions[:MAX_QUESTIONS]
    result.exploration = result.exploration[:MAX_EXPLORATION]
    return result
