"""Rotas da vida contínua (Fase 9): o dia dele até agora, o diário e os projetos."""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.database.connection import connect, transaction
from app.database.repository import log_audit, utcnow
from app.life import day as day_mod
from app.life import diary as diary_mod
from app.life import projects as projects_mod

router = APIRouter(prefix="/api")


def _conn(request: Request):
    return connect(request.app.state.settings.db_path)


@router.get("/life/today")
def today(request: Request):
    now = datetime.now(timezone.utc)
    d = diary_mod.today_local(now, None)
    start, end = day_mod.bounds(d, None)
    conn = _conn(request)
    try:
        f = day_mod.facts(conn, start, end)
        return {"day": d.isoformat(), "lines": [l[2:] for l in day_mod.render(f).splitlines() if l.startswith("- ")],
                "empty": day_mod.is_empty(f)}
    finally:
        conn.close()


@router.get("/diary")
def diary(request: Request, limit: int = 30):
    conn = _conn(request)
    try:
        return {"pages": diary_mod.listing(conn, max(1, min(limit, 365)))}
    finally:
        conn.close()


@router.get("/projects")
def projects(request: Request):
    conn = _conn(request)
    try:
        return {"projects": projects_mod.listing(conn)}
    finally:
        conn.close()


@router.post("/projects/{project_id}/drop")
def drop(request: Request, project_id: int):
    conn = _conn(request)
    try:
        with transaction(conn):
            if not projects_mod.close(conn, project_id, "dropped", "largado pelo usuário", utcnow()):
                raise HTTPException(404, "Projeto não encontrado ou já fechado.")
            log_audit(conn, "project.dropped", {"project_id": project_id, "by": "user"})
    finally:
        conn.close()
    return {"id": project_id, "status": "dropped"}
