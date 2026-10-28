"""Rosto do Nym (Fase 10): olhos de robô que mostram o estado real e reagem à conversa.

Duas camadas:
1. ESTADO (de base, sempre calculado pelo código a partir de registros reais): sonhando, dormindo, esperando você,
   trabalhando, cansado, com fome, curioso ou neutro. Nada é sorteado.
2. REAÇÃO (curta, alguns segundos por cima do estado): o que a conversa causou nele.
   - O próprio Nym escolhe: o modelo pode terminar a resposta com `[[rosto: surpreso]]` (marca escondida, como
     `[[fazer: ...]]`). É a autonomia de reagir: surpresa, risada, irritação, carinho... O CÓDIGO só aceita palavras da
     lista e grava a escolha (`face_events`), para dar para ver depois por que ele fez aquela cara.
   - Se ele não escolheu, o código lê a fala do dono (risada, carinho, notícia boa, desabafo, xingamento) e reage por ele.

Honestidade: é EXPRESSÃO de um bichinho digital, escolhida pelo modelo ou calculada pelo código. Não é prova de
sentimento; a interface e o prompt dizem isso. "Carinho" (olhos de coração) é afeto de vpet, não romance.
O mesmo vocabulário serve para a telinha física futura (ESP32 + OLED): ela só recebe estas palavras.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from app.memory.text import normalize

# Reações que o Nym (ou o código) pode mostrar por cima do estado.
REACTIONS: dict[str, str] = {
    "feliz": "feliz",
    "risada": "rindo",
    "surpreso": "surpreso",
    "irritado": "irritado",
    "triste": "triste",
    "curioso": "curioso",
    "confuso": "confuso",
    "carinho": "com carinho (olhos de coração)",
    "assustado": "assustado",
    "cansado": "cansado",
    "envergonhado": "envergonhado",
    "neutro": "neutro",
}
# Estados de base (só o código decide).
STATES: dict[str, str] = {
    "neutro": "à toa", "sonhando": "sonhando (Somnium)", "dormindo": "dormindo (vpet)", "esperando": "esperando você decidir",
    "trabalhando": "trabalhando num objetivo", "cansado": "energia baixa (estado calculado)", "fome": "com fome (vpet)",
    "curioso": "curioso (estado calculado)", "pensando": "pensando na resposta", "falando": "falando com você",
    "fora": "fora do ar",
}
ALIASES = {
    "alegre": "feliz", "contente": "feliz", "animado": "feliz", "empolgado": "feliz", "feliz": "feliz",
    "rindo": "risada", "riso": "risada", "risada": "risada", "gargalhada": "risada",
    "surpresa": "surpreso", "surpreso": "surpreso", "chocado": "surpreso", "espantado": "surpreso",
    "bravo": "irritado", "raiva": "irritado", "irritado": "irritado", "irritacao": "irritado", "zangado": "irritado",
    "chateado": "triste", "triste": "triste", "tristeza": "triste", "magoado": "triste",
    "curioso": "curioso", "curiosidade": "curioso", "interessado": "curioso",
    "confuso": "confuso", "duvida": "confuso", "perdido": "confuso",
    "carinho": "carinho", "apaixonado": "carinho", "amor": "carinho", "coracao": "carinho", "afeto": "carinho",
    "assustado": "assustado", "medo": "assustado", "susto": "assustado",
    "cansado": "cansado", "sono": "cansado", "exausto": "cansado",
    "envergonhado": "envergonhado", "vergonha": "envergonhado", "timido": "envergonhado",
    "neutro": "neutro", "normal": "neutro", "calmo": "neutro",
}
REACTION_SECONDS = 12          # quanto tempo a reação fica por cima do estado
TAG_RE = re.compile(r"\[\[\s*rosto\s*:\s*([^\]\n]{1,40}?)\s*\]\]", re.I)
SOURCES = ("model", "dono", "sistema")

# Leitura da fala do dono (só quando o Nym não escolheu a própria reação). Heurística por palavras.
_FROM_USER: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("irritado", re.compile(r"\b(idiota|burro|inutil|lixo|cala a boca|calado|odeio voce|te odeio|imbecil|otario)\b")),
    ("carinho", re.compile(r"\b(te amo|amo voce|gosto (?:muito )?de voce|te adoro|fofo|fofinho|meu amigo|melhor amigo|"
                           r"abraco|carinho|obrigad[oa] por existir|voce e demais)\b")),
    ("triste", re.compile(r"\b(to triste|estou triste|chorei|chorando|morreu|perdi|dia ruim|dia pessimo|to mal|estou mal|"
                          r"saudade)\b")),
    ("surpreso", re.compile(r"\b(adivinha|nao acredita|voce nao vai acreditar|olha isso|olha so|caramba|uau|nossa)\b")),
    ("feliz", re.compile(r"\b(consegui|deu certo|passei|fui aprovad[oa]|ganhei|boa noticia|to feliz|estou feliz)\b")),
    ("risada", re.compile(r"\b(k{3,}|(?:ha){2,}h?|(?:rs){2,}|rsrs+|lol)\b")),
)


def normalize_reaction(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    word = normalize(value).strip(" .!?\"'").split(" ")[0] if value.strip() else ""
    return ALIASES.get(word)


def extract(raw: str) -> str | None:
    """A reação que o modelo escolheu (a última marca válida). Palavra fora da lista = ignorada."""
    found = None
    for m in TAG_RE.finditer(raw or ""):
        found = normalize_reaction(m.group(1)) or found
    return found


def strip_tags(text: str) -> str:
    return TAG_RE.sub("", text or "")


def from_user_text(text: str) -> str | None:
    said = normalize(text or "")
    for name, pattern in _FROM_USER:
        if pattern.search(said):
            return name
    if (text or "").count("!") >= 3:
        return "surpreso"
    return None


def react(raw: str, user_text: str) -> tuple[str, str] | None:
    """(reação, origem): a escolha do próprio Nym vence; senão o código lê a fala do dono."""
    chosen = extract(raw)
    if chosen:
        return chosen, "model"
    read = from_user_text(user_text)
    return (read, "dono") if read else None


def record(conn: sqlite3.Connection, expression: str, source: str, message_id: int | None, now: datetime) -> int:
    if expression not in REACTIONS or source not in SOURCES:
        raise ValueError("reação ou origem inválida")
    cur = conn.execute("INSERT INTO face_events (expression, source, message_id, created_at) VALUES (?, ?, ?, ?)",
                       (expression, source, message_id, now.astimezone(timezone.utc).isoformat(timespec="milliseconds")))
    return int(cur.lastrowid)


def last_reaction(conn: sqlite3.Connection, now: datetime, seconds: int = REACTION_SECONDS) -> dict[str, Any] | None:
    since = (now - timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat(timespec="milliseconds")
    row = conn.execute("SELECT id, expression, source, message_id, created_at FROM face_events WHERE created_at >= ? "
                       "ORDER BY id DESC LIMIT 1", (since,)).fetchone()
    return dict(row) if row else None


def base_state(s: dict[str, Any]) -> str:
    """Estado de base a partir de sinais reais. Ordem = o que mais importa aparecer."""
    if s.get("dreaming"):
        return "sonhando"
    if s.get("pet_sleeping"):
        return "dormindo"
    if s.get("waiting"):
        return "esperando"
    if s.get("hungry"):
        return "fome"
    if s.get("energy") is not None and s["energy"] < 0.30:
        return "cansado"
    if s.get("working"):
        return "trabalhando"
    if s.get("curiosity") is not None and s["curiosity"] >= 0.60:
        return "curioso"
    return "neutro"


def signals(conn: sqlite3.Connection) -> dict[str, Any]:
    """Sinais que vêm do banco (o resto, como sonho rodando e estado calculado, quem chama completa)."""
    waiting = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'awaiting_permission'").fetchone()[0]
    waiting += conn.execute("SELECT COUNT(*) FROM goals WHERE mode = 'task' AND status = 'proposed'").fetchone()[0]
    working = conn.execute("SELECT COUNT(*) FROM goals WHERE mode = 'task' AND status = 'active'").fetchone()[0]
    return {"waiting": waiting > 0, "working": working > 0}


def snapshot(conn: sqlite3.Connection, now: datetime, extra: dict[str, Any]) -> dict[str, Any]:
    s = {**signals(conn), **extra}
    state = base_state(s)
    reaction = last_reaction(conn, now)
    return {"state": state, "state_label": STATES[state], "reaction": reaction,
            "reaction_label": REACTIONS.get(reaction["expression"]) if reaction else None,
            "note": "Expressão calculada pelo código ou escolhida pelo Nym; não é prova de sentimento."}
