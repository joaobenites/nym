"""Rotas do ateliê, das propostas de código e das lições (Fase 7). Tudo aqui é ação do usuário."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.creations.service import AtelierError, AtelierNotFound

router = APIRouter(prefix="/api")


class Reaction(BaseModel):
    target: str
    id: int
    value: int
    comment: str | None = None


def _svc(request: Request):
    return request.app.state.atelier


def _call(fn, *args):
    try:
        return fn(*args)
    except AtelierNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except AtelierError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/creations")
def list_creations(request: Request, kind: str | None = None, archived: bool = False):
    return {"creations": _call(_svc(request).creations, kind, archived)}


@router.get("/creations/{creation_id}")
def get_creation(request: Request, creation_id: int):
    return _call(_svc(request).creation, creation_id)


@router.post("/creations/{creation_id}/archive")
def archive_creation(request: Request, creation_id: int):
    return _call(_svc(request).archive, creation_id)


@router.post("/feedback")
def react(request: Request, body: Reaction):
    return {"feedback": _call(_svc(request).react, body.target, body.id, body.value, body.comment)}


@router.get("/proposals")
def list_proposals(request: Request):
    return {"proposals": _call(_svc(request).proposals)}


@router.post("/proposals/{proposal_id}/{action}")
def decide_proposal(request: Request, proposal_id: int, action: str):
    if action not in ("apply", "reject", "revert"):
        raise HTTPException(404, "Ação desconhecida.")
    return _call(_svc(request).decide, proposal_id, action)


@router.get("/lessons")
def list_lessons(request: Request):
    return {"lessons": _call(_svc(request).lessons)}


@router.post("/lessons/{lesson_id}/{action}")
def set_lesson(request: Request, lesson_id: int, action: str):
    if action not in ("retire", "restore"):
        raise HTTPException(404, "Ação desconhecida.")
    return _call(_svc(request).set_lesson, lesson_id, action == "restore")
