from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.presence import PresenceInputError

router = APIRouter(prefix="/api")


class PresenceUpdate(BaseModel):
    enabled: bool | None = None
    quiet_start: str | None = None
    quiet_end: str | None = None
    max_per_day: int | None = None
    min_gap_minutes: int | None = None
    welcome_back_hours: int | None = None


class TickRequest(BaseModel):
    opened: bool = False


@router.get("/state")
def get_state(request: Request):
    return asdict(request.app.state.state.current())


@router.get("/growth")
def get_growth(request: Request):
    """Estágio de convivência (vpet), calculado de dados reais."""
    from app.core import growth
    from app.database.connection import connect
    conn = connect(request.app.state.settings.db_path)
    try:
        return growth.compute(conn)
    finally:
        conn.close()


@router.get("/tastes")
def get_tastes(request: Request):
    """Gostos do Nym (calculados de registros reais), com as evidências de cada um."""
    from app.core import tastes
    from app.database.connection import connect, transaction
    from datetime import datetime, timezone
    conn = connect(request.app.state.settings.db_path)
    try:
        now = datetime.now(timezone.utc)
        if tastes.is_stale(conn, now, minutes=1):
            with transaction(conn):
                tastes.refresh(conn, now)
        return {"note": tastes.NOTE, "tastes": tastes.with_evidence(conn)}
    finally:
        conn.close()


@router.post("/tastes/{taste_id}/veto")
def veto_taste(taste_id: int, request: Request):
    """O usuário diz que aquele gosto não é do Nym: ele nunca mais é formado."""
    return _set_taste(taste_id, "vetoed", request)


@router.post("/tastes/{taste_id}/restore")
def restore_taste(taste_id: int, request: Request):
    """Desfaz o veto: o gosto volta a ser calculado das evidências."""
    return _set_taste(taste_id, "faded", request)


def _set_taste(taste_id: int, status: str, request: Request):
    from app.core import tastes
    from app.database.connection import connect, transaction
    from datetime import datetime, timezone
    conn = connect(request.app.state.settings.db_path)
    try:
        with transaction(conn):
            try:
                return tastes.set_status(conn, taste_id, status, datetime.now(timezone.utc))
            except tastes.TasteError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
    finally:
        conn.close()


@router.get("/followups")
def get_followups(request: Request):
    """Coisas que ele quer te perguntar depois (de coisas que você contou)."""
    from app.database.connection import connect
    conn = connect(request.app.state.settings.db_path)
    try:
        return [dict(r) for r in conn.execute("SELECT id, text, about, due_at, status FROM followups "
                                              "ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, due_at DESC LIMIT 30")]
    finally:
        conn.close()


@router.post("/followups/{followup_id}/dismiss")
def dismiss_followup(followup_id: int, request: Request):
    from app.database.connection import connect, transaction
    conn = connect(request.app.state.settings.db_path)
    try:
        with transaction(conn):
            cur = conn.execute("UPDATE followups SET status = 'dismissed' WHERE id = ? AND status = 'pending'", (followup_id,))
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail="Lembrete não encontrado ou já entregue.")
        return {"dismissed": True}
    finally:
        conn.close()


@router.get("/presence/settings")
def get_presence(request: Request):
    return request.app.state.presence.get_settings()


@router.put("/presence/settings")
def put_presence(body: PresenceUpdate, request: Request):
    try:
        return request.app.state.presence.update_settings(**body.model_dump(exclude_none=True))
    except PresenceInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/presence/tick")
def tick(body: TickRequest, request: Request):
    """A interface chama ao abrir e de tempos em tempos. Devolve a mensagem que o Nym enviou por iniciativa, se houver."""
    return {"message": request.app.state.presence.tick(opened=body.opened)}
