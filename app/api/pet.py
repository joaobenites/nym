"""Cuidados do vpet (Fase 10). As regras e os números ficam em app/core/pet.py; aqui só entrada/saída."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core import pet
from app.database.connection import connect, transaction
from app.database.repository import log_audit

router = APIRouter(prefix="/api/pet")


class ActionBody(BaseModel):
    choice: str | None = None     # pedra | papel | tesoura (só para brincar)
    hours: int | None = None      # só para dormir (1..10)


def _now_tz(request: Request):
    clock = getattr(request.app.state, "clock", None)
    from datetime import datetime, timezone
    now = clock() if callable(clock) else datetime.now(timezone.utc)
    return now, None


@router.get("")
def get_pet(request: Request):
    now, tz = _now_tz(request)
    conn = connect(request.app.state.settings.db_path)
    try:
        with transaction(conn):
            info = pet.status(conn, now, tz or now.astimezone().tzinfo)
        return info
    finally:
        conn.close()


@router.post("/{action}")
def do_action(action: str, body: ActionBody, request: Request):
    if action not in pet.ACTIONS:
        raise HTTPException(status_code=404, detail="Ação desconhecida.")
    now, tz = _now_tz(request)
    conn = connect(request.app.state.settings.db_path)
    try:
        try:
            with transaction(conn):
                result = pet.act(conn, action, now, tz or now.astimezone().tzinfo, choice=body.choice, hours=body.hours)
                log_audit(conn, "pet.action", {"action": action, "ok": result["ok"]})
        except pet.PetError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with transaction(conn):
            info = pet.status(conn, now, tz or now.astimezone().tzinfo)
        return {**result, "pet": info}
    finally:
        conn.close()
