from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.memory.store import InvalidMemoryInput, MemoryNotFound

router = APIRouter(prefix="/api/memories")


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except InvalidMemoryInput as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=1000)
    kind: str = "semantic"
    epistemic: str = "confirmed"
    importance: float = 0.7


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, max_length=1000)
    epistemic: str | None = None
    importance: float | None = None


class ConsolidateRequest(BaseModel):
    force: bool = False


# Rotas fixas ANTES de /{memory_id}, para não serem capturadas pelo parâmetro.
@router.get("/jobs")
def list_jobs(request: Request, limit: int = 50):
    return request.app.state.memory.jobs(limit)


@router.get("/retrieve")
def retrieve_memories(request: Request, q: str, limit: int = 8):
    """Mostra o que seria recuperado para uma consulta, com a nota de cada componente."""
    return request.app.state.memory.retrieve(q, max(1, min(limit, 30)))


@router.get("/identity-candidates")
def identity_candidates(request: Request):
    return request.app.state.memory.identity_candidates()


@router.post("/consolidate")
def consolidate(body: ConsolidateRequest, request: Request):
    return request.app.state.consolidator.run_once(force=body.force)


@router.post("/dedupe")
def dedupe(request: Request, dry_run: bool = True):
    return _call(request.app.state.memory.dedupe, dry_run)


@router.get("")
def list_memories(request: Request, kind: str | None = None, epistemic: str | None = None,
                  status: str | None = "active", q: str | None = None, limit: int = 100, offset: int = 0):
    return _call(request.app.state.memory.list, kind=kind, epistemic=epistemic,
                 status=None if status == "all" else status, q=q, limit=limit, offset=max(0, offset))


@router.post("")
def create_memory(body: MemoryCreate, request: Request):
    return _call(request.app.state.memory.create, body.content, body.kind, body.epistemic, body.importance)


@router.get("/{memory_id}")
def get_memory(memory_id: int, request: Request):
    return _call(request.app.state.memory.get, memory_id)


@router.patch("/{memory_id}")
def update_memory(memory_id: int, body: MemoryUpdate, request: Request):
    return _call(request.app.state.memory.update, memory_id, content=body.content,
                 epistemic=body.epistemic, importance=body.importance)


@router.post("/{memory_id}/archive")
def archive_memory(memory_id: int, request: Request):
    return _call(request.app.state.memory.archive, memory_id)


@router.post("/{memory_id}/restore")
def restore_memory(memory_id: int, request: Request):
    return _call(request.app.state.memory.restore, memory_id)


@router.delete("/{memory_id}")
def delete_memory(memory_id: int, request: Request, purge_sources: bool = False):
    """Exclusão definitiva. Com purge_sources, apaga também as mensagens de origem."""
    return _call(request.app.state.memory.delete, memory_id, purge_sources)
