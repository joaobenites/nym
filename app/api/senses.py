"""Rotas dos sentidos (Fase 8): o que ele consegue perceber agora e o registro do que já viu/assistiu."""
from fastapi import APIRouter, HTTPException, Request

from app.database.connection import connect, transaction
from app.database.repository import log_audit
from app.senses import video, vision

router = APIRouter(prefix="/api")


@router.get("/senses")
def senses_status(request: Request):
    caps = video.capabilities()
    see = request.app.state.vision.status()
    hear = "legendas do YouTube" if caps["yt_dlp"] else "nada ainda (instale o yt-dlp para ouvir pelas legendas)"
    return {
        "vision": see,
        "camera": {"available": see["available"], "detail": "só quando você clica no botão da câmera"},
        "screen": {"available": see["available"], "detail": "só quando você clica e escolhe o que mostrar"},
        "video": {**caps, "hearing": hear,
                  "frames": "quadros do vídeo" if caps["yt_dlp"] and caps["ffmpeg"] else
                            "só miniaturas (instale yt-dlp e ffmpeg para ver quadros do vídeo)"},
    }


@router.get("/perceptions")
def list_perceptions(request: Request, limit: int = 100):
    conn = connect(request.app.state.settings.db_path)
    try:
        return {"perceptions": vision.listing(conn, max(1, min(limit, 300)))}
    finally:
        conn.close()


@router.delete("/perceptions/{perception_id}")
def delete_perception(request: Request, perception_id: int):
    conn = connect(request.app.state.settings.db_path)
    try:
        with transaction(conn):
            if not vision.delete(conn, perception_id):
                raise HTTPException(404, "Percepção não encontrada.")
            log_audit(conn, "senses.deleted", {"perception_id": perception_id, "by": "user"})
    finally:
        conn.close()
    return {"deleted": perception_id}
