"""Extração de memórias a partir de um trecho de conversa (prompt + validação estrita).

O modelo PROPÕE; o código DECIDE. Regras impostas aqui, independentemente do que o modelo
escreva: todo item precisa citar ids de mensagens reais do trecho; "dito pelo usuário" só vale
se a evidência inclui mensagem do usuário (senão vira hipótese); segredos são descartados;
a quantidade de itens é limitada.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.memory.text import looks_sensitive
from app.models.base import ChatMessage

MAX_FACTS = 8
MAX_IDENTITY_NOTES = 3
MAX_ITEM_CHARS = 300
MAX_MESSAGE_CHARS_IN_PROMPT = 1200

SYSTEM_PROMPT = """Você analisa um trecho de conversa entre o Usuário e o Nym e produz um registro de memória em JSON.
Responda SOMENTE com um objeto JSON, sem nenhum texto fora dele, neste formato:
{
  "summary": "2 a 4 frases em português dizendo o que aconteceu no trecho",
  "outcome": "resultado ou decisão, ou null",
  "importance": 0.0 a 1.0,
  "participants": ["pessoas ou coisas envolvidas, além do usuário e do Nym"],
  "facts": [
    {"text": "frase curta e autônoma", "evidence": [ids das mensagens], "kind": "stated" ou "inferred",
     "confidence": 0.0 a 1.0, "importance": 0.0 a 1.0, "contradicts": [ids de memórias já registradas]}
  ],
  "identity_notes": [
    {"text": "observação sobre como o usuário gosta que o Nym seja ou sobre a relação", "evidence": [ids], "confidence": 0.0 a 1.0}
  ],
  "followups": [
    {"text": "como o Nym perguntaria depois, curto (ex.: \"E aí, como foi a prova?\")", "about": "assunto em 2 a 4 palavras",
     "due": "AAAA-MM-DD a partir de quando perguntar, ou null", "evidence": [ids]}
  ]
}
Regras:
- "facts": apenas informações que o USUÁRIO disse sobre si, sua vida, seus projetos, gostos ou planos e que valem ser lembradas depois. Escreva "O usuário ..." (ex.: "O usuário tem um gato chamado Mia").
- "kind": "stated" se o usuário disse diretamente; "inferred" se foi você quem deduziu (será tratada como hipótese).
- "evidence": obrigatório; use os números entre colchetes das mensagens que sustentam o item.
- "contradicts": só se o fato contradiz uma memória da lista "Memórias já registradas".
- "identity_notes": só padrões com evidência clara (ex.: "O usuário prefere respostas curtas"). Pode ficar vazio.
- "followups": só coisas que o USUÁRIO contou que vão acontecer ou ficaram em aberto (prova, entrevista, viagem,
  consulta, um vídeo para publicar, um prazo). "due" é o dia do acontecimento ou o seguinte, calculado a partir da
  data do trecho. No máximo 2. Pode ficar vazio.
- Nunca registre senhas, documentos, números de cartão ou outros segredos. Não invente nada.
- O que o Nym disse serve só de contexto. Se não houver nada a registrar, devolva listas vazias."""


class ExtractionError(Exception):
    pass


@dataclass
class Item:
    text: str
    evidence: list[int]
    stated: bool
    confidence: float
    importance: float = 0.5
    contradicts: list[int] = field(default_factory=list)


@dataclass
class Followup:
    text: str
    about: str | None
    due: str | None          # AAAA-MM-DD, já validado
    evidence: list[int]


@dataclass
class Extraction:
    summary: str
    outcome: str | None
    importance: float
    participants: list[str]
    facts: list[Item]
    identity_notes: list[Item]
    followups: list[Followup] = field(default_factory=list)


def build_messages(rows: Sequence[Mapping[str, Any]], existing: Sequence[Mapping[str, Any]]) -> list[ChatMessage]:
    lines = []
    for r in rows:
        who = "Usuário" if r["role"] == "user" else "Nym"
        lines.append(f"[{r['id']}] {who}: {r['content'][:MAX_MESSAGE_CHARS_IN_PROMPT]}")
    parts = []
    if existing:
        parts.append("Memórias já registradas (id: texto):\n" + "\n".join(f"- {m['id']}: {m['content']}" for m in existing))
    when = ""
    try:
        from app.core.timesense import describe_now, parse_time
        when = f"Data do trecho: {describe_now(parse_time(rows[0]['created_at']).astimezone())}\n"
    except (IndexError, KeyError, TypeError, ValueError):
        pass  # sem data legível: o trecho segue sem ela (lembretes ficam sem dia)
    parts.append(when + "Trecho da conversa:\n" + "\n".join(lines))
    return [ChatMessage("system", SYSTEM_PROMPT), ChatMessage("user", "\n\n".join(parts))]


def _load_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    raise ExtractionError("A resposta do modelo não é um objeto JSON válido.")


load_json = _load_json  # reutilizado pelo Somnium


def _number(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return min(1.0, max(0.0, float(value)))


def _ids(value: Any, allowed: set[int]) -> list[int]:
    if not isinstance(value, list):
        return []
    return sorted({v for v in value if isinstance(v, int) and not isinstance(v, bool) and v in allowed})


def _items(raw: Any, limit: int, message_ids: set[int], user_ids: set[int], existing_ids: set[int], notes: bool) -> list[Item]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ExtractionError("Campo de itens não é uma lista.")
    out: list[Item] = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            continue
        text = " ".join(entry["text"].split())[:MAX_ITEM_CHARS]
        evidence = _ids(entry.get("evidence"), message_ids)
        if not text or not evidence or looks_sensitive(text):
            continue  # sem evidência real ou com cara de segredo: descartado
        stated = (not notes) and entry.get("kind") == "stated" and bool(set(evidence) & user_ids)
        confidence = _number(entry.get("confidence"), 0.7 if stated else 0.4)
        if not stated:
            confidence = min(confidence, 0.7)  # hipótese nunca chega a certeza
        out.append(Item(text, evidence, stated, confidence, _number(entry.get("importance"), 0.5),
                        _ids(entry.get("contradicts"), existing_ids) if not notes else []))
        if len(out) >= limit:
            break
    return out


def parse_extraction(raw: str, *, message_ids: set[int], user_ids: set[int], existing_ids: set[int]) -> Extraction:
    data = _load_json(raw)
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ExtractionError("Resumo ausente na resposta do modelo.")
    outcome = data.get("outcome")
    outcome = " ".join(outcome.split())[:500] if isinstance(outcome, str) and outcome.strip() else None
    participants = [str(p).strip()[:60] for p in data.get("participants") or [] if isinstance(p, str) and p.strip()][:8]
    return Extraction(
        summary=" ".join(summary.split())[:1000],
        outcome=outcome,
        importance=_number(data.get("importance"), 0.5),
        participants=participants,
        facts=_items(data.get("facts"), MAX_FACTS, message_ids, user_ids, existing_ids, notes=False),
        identity_notes=_items(data.get("identity_notes"), MAX_IDENTITY_NOTES, message_ids, user_ids, existing_ids, notes=True),
        followups=_followups(data.get("followups"), user_ids),
    )


MAX_FOLLOWUPS = 2


def _followups(raw: Any, user_ids: set[int]) -> list[Followup]:
    """Só vale com evidência numa mensagem do USUÁRIO (foi ele quem contou), sem segredo e com data legível."""
    import datetime as _dt
    out: list[Followup] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            continue
        text = " ".join(entry["text"].split())[:200]
        evidence = _ids(entry.get("evidence"), user_ids)
        if len(text) < 5 or not evidence or looks_sensitive(text):
            continue
        due = entry.get("due")
        try:
            due = _dt.date.fromisoformat(due).isoformat() if isinstance(due, str) else None
        except ValueError:
            due = None
        about = entry.get("about")
        about = " ".join(about.split())[:60] if isinstance(about, str) and about.strip() else None
        out.append(Followup(text, about, due, evidence))
        if len(out) >= MAX_FOLLOWUPS:
            break
    return out
