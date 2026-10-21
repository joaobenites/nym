"""Rotas da Fase 5: objetivos executáveis, tarefas, permissões e pausa geral.

Só o usuário chega aqui. O Nym não tem rota para mudar permissões nem para aprovar as próprias tarefas.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.tasks.manager import TaskInputError, TaskNotFound
from app.tools.registry import PermissionInputError

router = APIRouter(prefix="/api/tasks")


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (TaskInputError, PermissionInputError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class GoalCreate(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    completion_criteria: str | None = Field(default=None, max_length=500)
    priority: int = 3
    deadline: str | None = None


class PermissionUpdate(BaseModel):
    mode: str


class FolderCreate(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    label: str | None = Field(default=None, max_length=60)


class AutonomyUpdate(BaseModel):
    enabled: bool | None = None
    auto_start: bool | None = None
    max_active: int | None = None
    max_per_day: int | None = None
    interval_hours: int | None = None
    max_per_run: int | None = None
    self_retries: int | None = None
    free_time_minutes: int | None = None


@router.get("")
def overview(request: Request):
    return request.app.state.tasks.overview()


@router.post("/goals")
def create_goal(body: GoalCreate, request: Request):
    return _call(request.app.state.tasks.create_goal, body.description, body.completion_criteria, body.priority,
                 body.deadline, "user")


@router.get("/goals/{goal_id}")
def get_goal(goal_id: int, request: Request):
    return _call(request.app.state.tasks.get_goal, goal_id)


@router.post("/goals/{goal_id}/cancel")
def cancel_goal(goal_id: int, request: Request):
    return _call(request.app.state.tasks.cancel_goal, goal_id)


@router.post("/goals/{goal_id}/approve")
def approve_goal(goal_id: int, request: Request):
    """Aprova um objetivo que o Nym propôs (só quando "começar sozinho" está desligado)."""
    return _call(request.app.state.tasks.approve_goal, goal_id)


@router.get("/folders")
def list_folders(request: Request):
    return request.app.state.tasks.folders()


@router.post("/folders")
def add_folder(body: FolderCreate, request: Request):
    """Autoriza o Nym a LER uma pasta sua (só leitura, só arquivos de texto)."""
    return _call(request.app.state.tasks.add_folder, body.path, body.label)


@router.delete("/folders/{folder_id}")
def remove_folder(folder_id: int, request: Request):
    return {"removed": _call(request.app.state.tasks.remove_folder, folder_id)}


@router.get("/research")
def research(request: Request, limit: int = 50):
    """Diário de pesquisas: cada busca e página lida, com fonte e data."""
    return request.app.state.tasks.research(limit)


@router.get("/autonomy")
def get_autonomy(request: Request):
    return {"settings": request.app.state.tasks.get_autonomy(), "runs": request.app.state.tasks.desire_runs(20)}


@router.put("/autonomy")
def put_autonomy(body: AutonomyUpdate, request: Request):
    return _call(request.app.state.tasks.update_autonomy, **body.model_dump(exclude_none=True))


@router.post("/goals/{goal_id}/resume")
def resume_goal(goal_id: int, request: Request):
    return _call(request.app.state.tasks.resume_goal, goal_id)


@router.post("/pause")
def pause(request: Request):
    return {"paused": request.app.state.tasks.set_paused(True)}


@router.post("/resume")
def resume(request: Request):
    return {"paused": request.app.state.tasks.set_paused(False)}


@router.post("/permissions/trust-medium")
def trust_medium(request: Request):
    """Um clique: tudo de risco médio roda sem perguntar (risco alto continua travado)."""
    return {"tools": request.app.state.tasks.trust_medium()}


@router.put("/permissions/{tool}")
def set_permission(tool: str, body: PermissionUpdate, request: Request):
    return _call(request.app.state.tasks.set_permission, tool, body.mode)


@router.post("/{task_id}/{action}")
def task_action(task_id: int, action: str, request: Request):
    tasks = request.app.state.tasks
    fn = {"approve": tasks.approve_task, "deny": tasks.deny_task, "cancel": tasks.cancel_task, "retry": tasks.retry_task}.get(action)
    if fn is None:
        raise HTTPException(status_code=400, detail="Ação inválida: use approve, deny, cancel ou retry.")
    return _call(fn, task_id)
