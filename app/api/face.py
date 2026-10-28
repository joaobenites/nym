"""Rosto do Nym (Fase 10): GET /api/face devolve o estado de base e a última reação. As regras ficam em app/core/face.py.

Usado pela página de rosto em tela cheia (face.html), que fica perguntando a cada segundo, e no futuro pela telinha
física (ESP32), que só precisa destas palavras. Nada aqui chama o modelo.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.core import face, pet
from app.database.connection import connect, transaction

router = APIRouter(prefix="/api/face")


@router.get("")
def get_face(request: Request):
    st = request.app.state
    clock = getattr(st, "clock", None)
    now = clock() if callable(clock) else datetime.now(timezone.utc)
    extra = {}
    try:
        extra["dreaming"] = bool(st.dream_scheduler.status().get("running"))
    except Exception:  # noqa: BLE001 - o rosto nunca derruba nada: sem o sinal, fica sem ele
        pass
    try:
        inner = st.state.current()
        extra["energy"], extra["curiosity"] = inner.energy, inner.curiosity
    except Exception:  # noqa: BLE001
        pass
    conn = connect(st.settings.db_path)
    try:
        try:
            with transaction(conn):
                info = pet.status(conn, now, now.astimezone().tzinfo)
            extra["pet_sleeping"] = info["sleeping"]
            extra["hungry"] = info["fullness"] < pet.CALL_AT["fome"] and not info["paused"]
        except Exception:  # noqa: BLE001
            pass
        return face.snapshot(conn, now, extra)
    finally:
        conn.close()
