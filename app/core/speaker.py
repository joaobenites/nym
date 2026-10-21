"""Quem está falando (Fase 10): o dono ou uma visita que usa a mesma janela de chat.

Achado do uso real (18/10): uma visita se apresentou ("aqui é o Claudio") e, horas depois, o Nym chamava o dono pelo
nome da visita nas mensagens que puxou sozinho. Um amigo sabe com quem está falando.

Tudo aqui é determinístico e vem de registros (nada de modelo):
- o dono é o nome que o próprio usuário disse ser dele ("eu me chamo João", "meu nome é João"); guardado em
  `app_state.owner_name` na primeira vez e nunca trocado sozinho depois;
- uma apresentação com outro nome ("aqui é o Claudio", "sou a Ana") faz dessa pessoa a visita atual;
- a visita vale enquanto a conversa continua; depois de `GUEST_TTL_SECONDS` sem mensagens, volta a ser o dono;
- apresentar-se com o nome do dono, ou dizer "sou o dono", encerra a visita na hora.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

from app.memory.text import normalize

OWNER_KEY = "owner_name"
GUEST_KEY = "speaker_guest"          # JSON simples "nome|ultima_fala_iso"
GUEST_TTL_SECONDS = 45 * 60

_NAME = r"([a-z][a-z]{1,19})"
_OWNER_INTRO = re.compile(r"\b(?:eu )?(?:me chamo|meu nome e|meu nome eh|pode me chamar de|sou o usuario[, ]+(?:o )?)\s*" + _NAME)
_OWNER_INTRO2 = re.compile(r"\b(?:eu )?me chamo " + _NAME + r"[, ]+(?:sou|e) (?:o |a )?usuari")
_GUEST_INTRO = re.compile(
    r"\b(?:aqui (?:e|eh) (?:o |a )?|sou (?:o |a )|aqui quem fala e (?:o |a )?|quem fala e (?:o |a )?|"
    r"meu nome e |me chamo )" + _NAME)
_NOT_NAMES = frozenset({
    "usuario", "dono", "criador", "novo", "nova", "outro", "outra", "eu", "voce", "nym", "um", "uma", "amigo", "amiga",
    "ia", "claude", "pessoa", "cara", "mesmo", "mesma", "teu", "seu", "dele", "ela", "ele", "aqui", "de", "do", "da",
    "so", "tambem", "sozinho", "sozinha", "novamente", "pai", "mae", "irmao", "irma", "primo", "prima", "tio", "tia",
    "desenvolvedor", "programador", "testador", "responsavel",
})
_IS_OWNER = re.compile(r"\b(?:sou o dono|sou eu o dono|aqui e o dono|sou o criador|sou o usuario)\b")


def _clean(name: str) -> str | None:
    n = normalize(name)
    if not n or n in _NOT_NAMES or len(n) < 2:
        return None
    return n


def _display(raw_text: str, norm_name: str) -> str:
    """Forma escrita como o usuário digitou (com acento e maiúscula), quando dá para achar no texto."""
    for w in re.findall(r"[A-Za-zÀ-ÿ]+", raw_text):
        if normalize(w) == norm_name:
            return w[:1].upper() + w[1:].lower()
    return norm_name.capitalize()


def get_owner(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (OWNER_KEY,)).fetchone()
    if row and row["value"]:
        return row["value"]
    return None


def _set_state(conn: sqlite3.Connection, key: str, value: str, now_iso: str) -> None:
    conn.execute("INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                 (key, value, now_iso))


def _del_state(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM app_state WHERE key = ?", (key,))


def backfill_owner(conn: sqlite3.Connection) -> str | None:
    """Banco que já existia: o dono é quem, nas primeiras falas, disse "eu me chamo X". Só roda se ainda não há dono."""
    if get_owner(conn):
        return get_owner(conn)
    for row in conn.execute("SELECT content, created_at FROM messages WHERE role = 'user' ORDER BY id LIMIT 60"):
        text = row["content"]
        wrote = normalize(text)
        for pattern in (_OWNER_INTRO2, _OWNER_INTRO):
            m = pattern.search(wrote)
            if m and _clean(m.group(1)):
                name = _display(text, _clean(m.group(1)))
                _set_state(conn, OWNER_KEY, name, row["created_at"])
                return name
    return None


def observe(conn: sqlite3.Connection, text: str, now: datetime, previous_at: str | None = None) -> None:
    """Chame uma vez por fala do usuário (dentro de uma transação). Atualiza dono e visita."""
    now_iso = now.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    wrote = normalize(text)
    owner = get_owner(conn) or backfill_owner(conn)
    if owner is None:
        for pattern in (_OWNER_INTRO2, _OWNER_INTRO):
            m = pattern.search(wrote)
            if m and _clean(m.group(1)):
                owner = _display(text, _clean(m.group(1)))
                _set_state(conn, OWNER_KEY, owner, now_iso)
                _del_state(conn, GUEST_KEY)
                return
    if _IS_OWNER.search(wrote):
        _del_state(conn, GUEST_KEY)
        return
    m = _GUEST_INTRO.search(wrote)
    if m:
        name = _clean(m.group(1))
        if name:
            if owner and normalize(owner) == name:
                _del_state(conn, GUEST_KEY)          # "aqui é o João": voltou o dono
            else:
                _set_state(conn, GUEST_KEY, f"{_display(text, name)}|{now_iso}", now_iso)
            return
    # sem apresentação: a visita continua se a conversa continua; senão volta o dono
    guest = current_guest(conn, now, previous_at=previous_at)
    if guest:
        _set_state(conn, GUEST_KEY, f"{guest}|{now_iso}", now_iso)
    else:
        _del_state(conn, GUEST_KEY)


def current_guest(conn: sqlite3.Connection, now: datetime, previous_at: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (GUEST_KEY,)).fetchone()
    if not row or "|" not in (row["value"] or ""):
        return None
    name, _, last = row["value"].partition("|")
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return None
    ref = now
    if (ref - last_dt).total_seconds() > GUEST_TTL_SECONDS:
        return None
    return name or None


def block(conn: sqlite3.Connection, now: datetime) -> str:
    """Linha de contexto sobre com quem ele está falando. Vazia se não há dono conhecido e nem visita."""
    owner = get_owner(conn)
    guest = current_guest(conn, now)
    if guest:
        base = (f"Quem está falando agora: {guest}, uma visita que usa a mesma janela. "
                + (f"Seu dono e parceiro é {owner}; o vínculo, o corpinho de vpet e as lembranças do dia a dia são com ele. "
                   f"Trate {guest} como alguém que você está conhecendo, sem fingir que {guest} é {owner}."
                   if owner else "Você ainda não sabe o nome do seu dono."))
        return base
    if owner:
        return f"Quem está falando: {owner}, seu dono e parceiro."
    return ""


def owner_line(conn: sqlite3.Connection) -> str:
    """Para mensagens que ele puxa sozinho: sempre é o dono quem recebe, nunca a visita."""
    owner = get_owner(conn)
    return f"Quem recebe esta mensagem é {owner}, seu dono e parceiro (nunca uma visita)." if owner else ""
