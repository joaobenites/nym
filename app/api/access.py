"""Rotas de acesso pelo celular (Fase 8.1). Gerar código e desconectar aparelhos: só no próprio computador."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core import access

router = APIRouter(prefix="/api")


class PairIn(BaseModel):
    code: str = Field(min_length=4, max_length=12)
    name: str | None = Field(default=None, max_length=60)


def _local_only(request: Request) -> None:
    if not access.is_loopback(request.client.host if request.client else None):
        raise HTTPException(403, "Só dá para fazer isso no próprio computador do Nym.")


@router.get("/access")
def access_info(request: Request):
    settings = request.app.state.settings
    local = access.is_loopback(request.client.host if request.client else None)
    info = {"local": local, "lan": settings.host not in ("127.0.0.1", "localhost", "::1"), "port": settings.port}
    if local:
        info["urls"] = [f"http://{ip}:{settings.port}" for ip in access.lan_addresses()] if info["lan"] else []
        info["devices"] = request.app.state.access.devices()
    else:
        info["device"] = getattr(request.state, "device", None)
    return info


@router.post("/access/code")
def new_code(request: Request):
    _local_only(request)
    return request.app.state.access.new_code()


@router.delete("/access/devices/{device_id}")
def revoke(request: Request, device_id: int):
    _local_only(request)
    if not request.app.state.access.revoke(device_id):
        raise HTTPException(404, "Aparelho não encontrado.")
    return {"revoked": device_id}


@router.post("/pair")
def pair(request: Request, body: PairIn):
    ip = request.client.host if request.client else "?"
    try:
        token = request.app.state.access.pair(body.code, body.name or "Celular", ip)
    except PermissionError as exc:
        raise HTTPException(429, str(exc)) from None
    if token is None:
        raise HTTPException(401, "Código errado ou vencido. Gere outro no computador.")
    resp = JSONResponse({"paired": True})
    resp.set_cookie(access.COOKIE, token, max_age=access.COOKIE_MAX_AGE, httponly=True, samesite="strict", path="/")
    return resp
