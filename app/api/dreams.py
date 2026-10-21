from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/dreams")


@router.get("")
def overview(request: Request, limit: int = 30):
    return {"status": request.app.state.dream_scheduler.status(), "cycles": request.app.state.dreams.list_cycles(limit)}


# Rotas fixas ANTES de /{cycle_id}.
@router.post("/run")
def run_now(request: Request):
    """Sonha agora (manual). Pode levar alguns segundos: o modelo precisa responder."""
    return request.app.state.dream_engine.run_cycle("manual", "pedido do usuário")


@router.post("/cancel")
def cancel(request: Request):
    return {"cancelled": request.app.state.dream_engine.cancel()}


@router.post("/pause")
def pause(request: Request):
    request.app.state.dreams.set_paused(True)
    return {"paused": True}


@router.post("/resume")
def resume(request: Request):
    request.app.state.dreams.set_paused(False)
    return {"paused": False}


@router.get("/questions")
def questions(request: Request):
    return request.app.state.dreams.open_questions()


@router.post("/questions/{item_id}/close")
def close_question(item_id: int, request: Request):
    if not request.app.state.dreams.close_question(item_id):
        raise HTTPException(status_code=404, detail="Pergunta não encontrada.")
    return {"closed": True}


@router.get("/exploration")
def exploration(request: Request, status: str | None = None):
    try:
        return request.app.state.dreams.explorations(status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/exploration/{task_id}/{action}")
def exploration_action(task_id: int, action: str, request: Request):
    status = {"approve": "approved", "dismiss": "dismissed"}.get(action)
    if status is None:
        raise HTTPException(status_code=400, detail="Ação inválida: use approve ou dismiss.")
    if not request.app.state.dreams.set_exploration(task_id, status):
        raise HTTPException(status_code=404, detail="Tarefa não encontrada ou já encerrada.")
    return {"status": status}


@router.get("/{cycle_id}")
def get_cycle(cycle_id: int, request: Request):
    cycle = request.app.state.dreams.get_cycle(cycle_id)
    if cycle is None:
        raise HTTPException(status_code=404, detail="Ciclo não encontrado.")
    return cycle
