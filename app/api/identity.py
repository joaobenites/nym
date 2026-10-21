from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.goals import GoalNotFoundError, InvalidGoalInput
from app.core.identity import (
    ChangeStateError,
    IdentityNotFoundError,
    InvalidIdentityInput,
    ProtectedEntryError,
)

router = APIRouter(prefix="/api")

_ERRORS = {
    InvalidIdentityInput: 400,
    InvalidGoalInput: 400,
    ProtectedEntryError: 403,
    IdentityNotFoundError: 404,
    GoalNotFoundError: 404,
    ChangeStateError: 409,
}


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except tuple(_ERRORS) as exc:
        raise HTTPException(status_code=_ERRORS[type(exc)], detail=str(exc))


class EntryCreate(BaseModel):
    kind: str
    value: str = Field(min_length=1, max_length=1000)
    justification: str | None = Field(default=None, max_length=1000)


class EntryUpdate(BaseModel):
    value: str = Field(min_length=1, max_length=1000)
    justification: str | None = Field(default=None, max_length=1000)


class GoalCreate(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    priority: int = 3


class GoalUpdate(BaseModel):
    status: str | None = None
    priority: int | None = None
    description: str | None = Field(default=None, max_length=500)


@router.get("/identity")
def get_identity(request: Request):
    return request.app.state.identity.snapshot()


@router.post("/identity/entries")
def create_entry(body: EntryCreate, request: Request):
    return _call(request.app.state.identity.add_entry, body.kind, body.value, body.justification)


@router.patch("/identity/entries/{entry_id}")
def update_entry(entry_id: int, body: EntryUpdate, request: Request):
    return _call(request.app.state.identity.update_entry, entry_id, body.value, body.justification)


@router.delete("/identity/entries/{entry_id}")
def archive_entry(entry_id: int, request: Request):
    """Arquiva (não apaga): a entrada sai do contexto, mas o histórico permanece."""
    return _call(request.app.state.identity.archive_entry, entry_id)


@router.get("/identity/changes")
def list_changes(request: Request, status: str | None = None, limit: int = 100):
    return _call(request.app.state.identity.list_changes, status, max(1, min(limit, 500)))


@router.post("/identity/changes/{change_id}/accept")
def accept_change(change_id: int, request: Request):
    return _call(request.app.state.identity.decide, change_id, True)


@router.post("/identity/changes/{change_id}/reject")
def reject_change(change_id: int, request: Request):
    return _call(request.app.state.identity.decide, change_id, False)


@router.get("/goals")
def list_goals(request: Request, status: str | None = None):
    return _call(request.app.state.goals.list_goals, status)


@router.post("/goals")
def create_goal(body: GoalCreate, request: Request):
    return _call(request.app.state.goals.add_goal, body.description, body.priority, "user")


@router.patch("/goals/{goal_id}")
def update_goal(goal_id: int, body: GoalUpdate, request: Request):
    return _call(
        request.app.state.goals.update_goal, goal_id,
        status=body.status, priority=body.priority, description=body.description,
    )
