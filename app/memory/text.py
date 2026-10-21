"""Normalização e similaridade de textos (puro, sem banco).

Simples de propósito: tokens sem acento, plural removido, prefixo de 6 letras como
"radical". É mensurável e previsível; embeddings podem entrar depois, se forem úteis.
"""
from __future__ import annotations

import re
import unicodedata

STOPWORDS = frozenset(
    "a o as os um uma uns umas de do da dos das em no na nos nas e ou que para por com se ao aos "
    "à às é são foi era ser ter tem the usuario usuaria nym ele ela eles elas seu sua seus suas "
    "meu minha meus minhas eu voce vc me te lhe isso isto esse essa este esta mais muito ja como "
    "quando onde tambem mas pra pro".split()
)
# Negações mudam o sentido: nunca são stopwords, e bloqueiam a detecção de duplicata.
NEGATIONS = frozenset({"nao", "nunca", "jamais", "nenhum", "nenhuma", "sem"})

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_ALNUM.sub(" ", stripped).strip()


def _stem(token: str) -> str:
    if token.isdigit():
        return token
    if len(token) > 4 and token.endswith("s"):
        token = token[:-1]
    return token[:6]


def token_set(text: str) -> frozenset[str]:
    out = set()
    for raw in normalize(text).split():
        if raw in STOPWORDS:
            continue
        if len(raw) < 2 and not raw.isdigit():
            continue
        out.add(raw if raw in NEGATIONS else _stem(raw))
    return frozenset(out)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_duplicate(text_a: str, text_b: str, threshold: float = 0.75) -> bool:
    """Textos que dizem a mesma coisa. Números ou negações diferentes nunca são duplicata."""
    sa, sb = token_set(text_a), token_set(text_b)
    if not sa or not sb:
        return False
    diff = sa ^ sb
    if any(any(ch.isdigit() for ch in t) for t in diff):
        return False
    if diff & NEGATIONS:
        return False
    return jaccard(sa, sb) >= threshold


def is_superset(longer: str, shorter: str) -> bool:
    """`longer` contém todos os tokens de `shorter` e mais algum (informação a mais)."""
    sl, ss = token_set(longer), token_set(shorter)
    return bool(ss) and ss < sl


SENSITIVE_PATTERNS = (
    re.compile(r"\d{9,}"),                                  # documentos, cartões, contas
    re.compile(r"\b\d{3}[.\s]?\d{3}[.\s]?\d{3}[-.\s]?\d{2}\b"),  # CPF formatado
    re.compile(r"\b(senha|password|passwd|token|api[\s_-]?key|chave secreta)\b", re.I),
)


def looks_sensitive(text: str) -> bool:
    """Evita guardar segredos e identificadores como memória. Heurística, não garantia."""
    compact = re.sub(r"(?<=\d)[\s.-](?=\d)", "", text)
    return any(p.search(text) or p.search(compact) for p in SENSITIVE_PATTERNS)
