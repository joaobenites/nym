"""Apoio aos testes de memória: chat com horários controlados e respostas de extração em JSON."""
import json
from datetime import datetime, timedelta, timezone

from app.database import repository as repo
from app.database.connection import connect, transaction

BASE = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)


def at(minutes):
    return (BASE + timedelta(minutes=minutes)).isoformat(timespec="milliseconds")


def clock_at(minutes):
    return lambda: BASE + timedelta(minutes=minutes)


def seed_chat(settings, items):
    """items: lista de (role, texto, minuto). Cria o chat único e devolve os ids das mensagens."""
    conn = connect(settings.db_path)
    try:
        with transaction(conn):
            cid = repo.get_state(conn, "main_conversation_id")
            if cid is None:
                cid = repo.create_conversation(conn, "Conversa com Nym")
                repo.set_state(conn, "main_conversation_id", cid)
            return [repo.add_message(conn, cid, role, text, created_at=at(minute)) for role, text, minute in items]
    finally:
        conn.close()


def extraction(summary="Conversa sobre a vida do usuário.", facts=(), notes=(), importance=0.6, outcome=None):
    return json.dumps({
        "summary": summary, "outcome": outcome, "importance": importance, "participants": [],
        "facts": list(facts), "identity_notes": list(notes),
    }, ensure_ascii=False)


def fact(text, evidence, kind="stated", confidence=0.9, importance=0.6, contradicts=()):
    return {"text": text, "evidence": list(evidence), "kind": kind, "confidence": confidence,
            "importance": importance, "contradicts": list(contradicts)}


def db_dump(settings):
    conn = connect(settings.db_path)
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()
