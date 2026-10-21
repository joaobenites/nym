"""Apoio aos testes do Somnium."""
import json

from app.database.connection import connect, transaction
from app.memory import store
from tests.memtools import at


def dream_reply(review="Revisão das memórias recentes do usuário.", associations=(), hypotheses=(), questions=(),
                exploration=(), note=None):
    return json.dumps({
        "review": review, "associations": list(associations), "hypotheses": list(hypotheses),
        "open_questions": list(questions), "exploration": list(exploration), "dream_note": note,
    }, ensure_ascii=False)


def add_mem(settings, content, *, kind="semantic", epistemic="confirmed", origin="user_stated", importance=0.5,
            confidence=0.8, recurrence=1, minutes=0, message_ids=(), access_count=0):
    conn = connect(settings.db_path)
    try:
        with transaction(conn):
            mid = store.create_memory(conn, kind=kind, content=content, epistemic=None if kind == "episodic" else epistemic,
                                      origin=origin, importance=importance, confidence=confidence, message_ids=message_ids)
            conn.execute("UPDATE memories SET updated_at = ?, occurred_at = ?, recurrence = ?, access_count = ? WHERE id = ?",
                         (at(minutes), at(minutes), recurrence, access_count, mid))
        return mid
    finally:
        conn.close()


def rows(settings, sql, params=()):
    conn = connect(settings.db_path)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
