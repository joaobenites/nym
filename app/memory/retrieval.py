"""Recuperação contextual de memórias.

A nota de cada memória combina componentes configuráveis (`RetrievalWeights`), todos em 0..1:
  lexical    cobertura ponderada (idf) das palavras da consulta
  recency    decaimento exponencial pela idade
  importance importância estimada
  recurrence quantas vezes foi reforçada (saturando)
  confidence confiança registrada
  goal       sobreposição com os objetivos ativos
  relation   vizinhança (ligações) de um resultado forte

Só entra no resultado o que tem relevância lexical mínima ou ligação com um resultado forte:
importância e recência sozinhas não trazem memória irrelevante. Exceção deliberada: poucos
"fatos centrais" (confirmados, de alta importância) sempre entram, como o nome do usuário.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Collection

from app.memory.text import token_set

UNKNOWN_TOKEN_WEIGHT = 0.25

EPISTEMIC_LABEL = {
    "confirmed": "confirmado",
    "sourced": "de fonte externa",
    "hypothesis": "hipótese",
    "unverified": "não verificado",
}


@dataclass(frozen=True)
class RetrievalWeights:
    lexical: float = 0.50
    recency: float = 0.10
    importance: float = 0.15
    recurrence: float = 0.05
    confidence: float = 0.10
    goal: float = 0.05
    relation: float = 0.05
    recency_half_life_days: float = 30.0
    min_lexical: float = 0.20
    core_slots: int = 3
    core_min_importance: float = 0.8


@dataclass
class Hit:
    memory: dict[str, Any]
    score: float
    components: dict[str, float]
    why: str  # "relevante" | "relacionada" | "central"
    conflicted: bool = False


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _coverage(query_idf: dict[str, float], tokens: frozenset[str]) -> float:
    total = sum(query_idf.values())
    if total <= 0:
        return 0.0
    return sum(w for t, w in query_idf.items() if t in tokens) / total


def retrieve(
    conn: sqlite3.Connection,
    query: str,
    *,
    weights: RetrievalWeights = RetrievalWeights(),
    limit: int = 8,
    exclude_message_ids: Collection[int] = (),
    now: datetime | None = None,
) -> list[Hit]:
    now = now or datetime.now(timezone.utc)
    rows = [dict(r) for r in conn.execute("SELECT * FROM memories WHERE status = 'active'")]
    if not rows:
        return []
    sources: dict[int, set[int]] = {}
    for r in conn.execute("SELECT memory_id, message_id FROM memory_sources WHERE message_id IS NOT NULL"):
        sources.setdefault(r["memory_id"], set()).add(r["message_id"])
    excluded = set(exclude_message_ids)
    if excluded:  # já está no contexto da conversa: não gasta orçamento repetindo
        rows = [m for m in rows if not (sources.get(m["id"]) and sources[m["id"]] <= excluded)]
    by_id = {m["id"]: m for m in rows}
    tokens = {m["id"]: token_set(m["content"]) for m in rows}

    n = len(rows)
    df: dict[str, int] = {}
    for toks in tokens.values():
        for t in toks:
            df[t] = df.get(t, 0) + 1
    qtokens = token_set(query)
    # Palavras da consulta que não existem em nenhuma memória não podem casar com nada:
    # pesam pouco, para não diluir as que casam.
    query_idf = {t: (math.log(1 + n / (1 + df[t])) if t in df else UNKNOWN_TOKEN_WEIGHT) for t in qtokens}

    goals = [r["description"] for r in conn.execute("SELECT description FROM goals WHERE status = 'active'")]
    goal_tokens = token_set(" ".join(goals))
    goal_idf = {t: 1.0 for t in goal_tokens}

    links = [dict(r) for r in conn.execute("SELECT from_id, to_id, relation FROM memory_links")]
    neighbors: dict[int, set[int]] = {}
    for link in links:
        neighbors.setdefault(link["from_id"], set()).add(link["to_id"])
        neighbors.setdefault(link["to_id"], set()).add(link["from_id"])
    conflicted = {l["from_id"] for l in links if l["relation"] == "contradicts" and l["to_id"] in by_id}
    conflicted |= {l["to_id"] for l in links if l["relation"] == "contradicts" and l["from_id"] in by_id}

    def components(m: dict[str, Any], lex: float, rel: float) -> dict[str, float]:
        age_days = max(0.0, (now - _parse_time(m["occurred_at"] if m["kind"] == "episodic" else m["updated_at"])).total_seconds() / 86400)
        return {
            "lexical": lex,
            "recency": 0.5 ** (age_days / weights.recency_half_life_days),
            "importance": m["importance"],
            "recurrence": min(1.0, math.log(1 + m["recurrence"]) / math.log(6)),
            "confidence": m["confidence"],
            "goal": _coverage(goal_idf, tokens[m["id"]]) if goal_idf else 0.0,
            "relation": rel,
        }

    def total(c: dict[str, float]) -> float:
        return (weights.lexical * c["lexical"] + weights.recency * c["recency"]
                + weights.importance * c["importance"] + weights.recurrence * c["recurrence"]
                + weights.confidence * c["confidence"] + weights.goal * c["goal"]
                + weights.relation * c["relation"])

    hits: dict[int, Hit] = {}
    for m in rows:
        lex = _coverage(query_idf, tokens[m["id"]])
        if lex >= weights.min_lexical:
            c = components(m, lex, 0.0)
            hits[m["id"]] = Hit(m, total(c), c, "relevante", m["id"] in conflicted)

    strong = sorted(hits.values(), key=lambda h: -h.score)[:3]  # expansão por relações
    for hit in strong:
        for nid in neighbors.get(hit.memory["id"], ()):
            if nid in by_id and nid not in hits:
                c = components(by_id[nid], 0.0, hit.components["lexical"])
                hits[nid] = Hit(by_id[nid], total(c), c, "relacionada", nid in conflicted)

    ranked = sorted(hits.values(), key=lambda h: (-h.score, h.memory["id"]))[:limit]

    core = [m for m in rows if m["kind"] == "semantic" and m["epistemic"] == "confirmed"
            and m["importance"] >= weights.core_min_importance]
    core.sort(key=lambda m: -(m["importance"] * m["confidence"]))
    present = {h.memory["id"] for h in ranked}
    for m in core[: weights.core_slots]:
        if m["id"] not in present:
            c = components(m, _coverage(query_idf, tokens[m["id"]]), 0.0)
            ranked.append(Hit(m, total(c), c, "central", m["id"] in conflicted))
    return ranked


def format_block(hits: list[Hit], budget_chars: int) -> tuple[str, list[int]]:
    """Texto para o prompt, com o estado epistêmico visível, e os ids realmente incluídos."""
    if not hits or budget_chars <= 0:
        return "", []
    lines, used_ids, used = [], [], 0
    for hit in hits:
        m = hit.memory
        date = _parse_time(m["occurred_at"]).strftime("%d/%m/%Y")
        if m["kind"] == "episodic":
            tag = f"episódio de {date}"
        else:
            tag = EPISTEMIC_LABEL.get(m["epistemic"], "não verificado")
            if m["kind"] == "identity":
                tag += ", observação"
            if hit.conflicted:
                tag += ", em conflito com outra lembrança"
        line = f"- [{tag}] {m['content']}"
        if used + len(line) > budget_chars:
            break
        lines.append(line)
        used_ids.append(m["id"])
        used += len(line) + 1
    if not lines:
        return "", []
    header = ("Lembranças relevantes (registros reais, com o grau de certeza entre colchetes; "
              "trate hipóteses como hipóteses e não invente além delas):")
    return header + "\n" + "\n".join(lines), used_ids


def hit_to_dict(hit: Hit) -> dict[str, Any]:
    m = dict(hit.memory)
    m["entities"] = json.loads(m["entities"]) if isinstance(m.get("entities"), str) else m.get("entities")
    return {"memory": m, "score": round(hit.score, 4), "components": {k: round(v, 4) for k, v in hit.components.items()},
            "why": hit.why, "conflicted": hit.conflicted}


def weights_dict(weights: RetrievalWeights) -> dict[str, Any]:
    return asdict(weights)
