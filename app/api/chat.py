from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
import json

from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.core.chat_service import MAX_MESSAGE_CHARS, ChatFailure, InvalidInputError

router = APIRouter(prefix="/api")


class ImageIn(BaseModel):
    data: str = Field(max_length=12_000_000)        # base64 ou data URL (até ~8 MB de imagem)
    kind: str = "image"                             # image | camera | screen
    name: str | None = Field(default=None, max_length=120)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    images: list[ImageIn] = Field(default_factory=list, max_length=3)


@router.post("/chat")
def post_chat(body: ChatRequest, request: Request):
    service = request.app.state.chat_service
    try:
        result = service.send_message(body.message, [i.model_dump() for i in body.images])
    except InvalidInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ChatFailure as exc:
        # 503: modelo indisponível (tentar de novo depois); 502: resposta inválida do modelo.
        return JSONResponse(
            status_code=503 if exc.unavailable else 502,
            content={
                "detail": str(exc),
                "user_message_id": exc.user_message_id,
                "retryable": exc.unavailable,
            },
        )
    return asdict(result)


@router.post("/chat/stream")
def post_chat_stream(body: ChatRequest, request: Request):
    """Resposta em pedaços (NDJSON: um objeto JSON por linha), para a interface mostrar o texto enquanto é gerado.

    Eventos: start, delta (texto), done (resposta salva), stopped (interrompida; o que saiu fica salvo) ou error."""
    service = request.app.state.chat_service
    try:
        events = service.stream_message(body.message, [i.model_dump() for i in body.images])
    except InvalidInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    def lines():
        for event in events:
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/chat/stop")
def stop_chat(request: Request):
    """Interrompe a resposta em andamento (o Ollama para de gerar)."""
    request.app.state.chat_service.stop()
    return {"stopped": True}


@router.get("/chat/history")
def get_history(request: Request, limit: int = 100, before_id: int | None = None):
    return request.app.state.chat_service.get_history(limit, before_id)
