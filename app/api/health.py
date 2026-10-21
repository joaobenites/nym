import sqlite3

from fastapi import APIRouter, Request

from app import __version__
from app.database.connection import connect

router = APIRouter()


@router.get("/health")
def health(request: Request):
    settings = request.app.state.settings
    provider = request.app.state.provider

    try:
        conn = connect(settings.db_path)
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        database = {"ok": True, "detail": "SQLite acessível."}
    except sqlite3.Error as exc:
        database = {"ok": False, "detail": str(exc)}

    status = provider.check_availability()
    return {
        "version": __version__,
        "database": database,
        "model": {
            "provider": provider.name,
            "name": provider.model,
            "available": status.available,
            "detail": status.detail,
        },
    }
