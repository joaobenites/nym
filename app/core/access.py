"""Acesso pelo celular na rede local, com pareamento (Fase 8.1).

Regras:
- No próprio computador (127.0.0.1 / ::1) nada muda: entra direto. Mas o cabeçalho Host precisa ser local
  (localhost, 127.0.0.1, [::1]): isso barra o ataque de "DNS rebinding", em que um site malicioso tenta falar com o
  Nym pelo navegador do usuário.
- De qualquer outro aparelho: só entra quem foi PAREADO. No computador você gera um código de 6 dígitos (vale 10
  minutos, uma vez, até 5 tentativas); no celular digita o código e recebe um token guardado num cookie HttpOnly.
  O banco guarda só o hash do token. Desconectar um aparelho vale na hora.
- Gerar códigos e desconectar aparelhos só pelo próprio computador: um celular pareado não consegue parear outro.
- Tentativas erradas demais de um mesmo endereço bloqueiam por 10 minutos.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from app.database.connection import transaction
from app.database.repository import log_audit

COOKIE = "nym_device"
COOKIE_MAX_AGE = 180 * 24 * 3600
CODE_TTL = 600
CODE_TRIES = 5
IP_FAILS = 10
IP_WINDOW = 600
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1", "testserver"}   # testserver: cliente de testes do Starlette
LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "testclient"}
PUBLIC_PATHS = {"/login.html", "/login.js", "/style.css", "/manifest.webmanifest", "/icon.svg", "/icon-192.png",
                "/icon-512.png", "/api/pair", "/favicon.ico"}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_loopback(client_host: str | None) -> bool:
    if not client_host:
        return False
    if client_host in LOOPBACK_CLIENTS:
        return True
    try:
        return ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        return False


def host_name(host_header: str | None) -> str:
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        return host[: host.find("]") + 1] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def lan_addresses() -> list[str]:
    """Endereços IPv4 privados deste computador (para montar o link do celular). Nenhum pacote é enviado."""
    found: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))      # UDP sem envio: só descobre a interface de saída
            found.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        found.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    out = []
    for ip in found:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.version == 4 and addr.is_private and not addr.is_loopback and not addr.is_link_local:
            out.append(ip)
    return sorted(out)


@dataclass
class _Code:
    digest: str
    expires: float
    tries: int = 0


class AccessGuard:
    def __init__(self, connect: Callable[[], sqlite3.Connection], now: Callable[[], str],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._connect = connect
        self._now = now
        self._clock = clock
        self._lock = threading.Lock()
        self._code: _Code | None = None
        self._fails: dict[str, list[float]] = {}
        self._seen: dict[int, float] = {}

    # --- pareamento ------------------------------------------------------------------------------------------
    def new_code(self) -> dict[str, Any]:
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self._lock:
            self._code = _Code(hash_token(code), self._clock() + CODE_TTL)
        return {"code": code, "expires_in": CODE_TTL}

    def blocked(self, ip: str) -> bool:
        now = self._clock()
        with self._lock:
            recent = [t for t in self._fails.get(ip, []) if now - t < IP_WINDOW]
            self._fails[ip] = recent
            return len(recent) >= IP_FAILS

    def _fail(self, ip: str) -> None:
        with self._lock:
            self._fails.setdefault(ip, []).append(self._clock())

    def pair(self, code: str, name: str, ip: str) -> str | None:
        """Código certo → cria o aparelho e devolve o token (só desta vez). Errado → None."""
        if self.blocked(ip):
            raise PermissionError("Tentativas demais. Espere 10 minutos.")
        code = "".join(ch for ch in str(code) if ch.isdigit())
        with self._lock:
            current = self._code
            ok = (current is not None and self._clock() < current.expires and current.tries < CODE_TRIES
                  and hmac.compare_digest(current.digest, hash_token(code)))
            if current is not None and not ok:
                current.tries += 1
            if ok:
                self._code = None                       # código de uso único
        if not ok:
            self._fail(ip)
            return None
        token = secrets.token_urlsafe(32)
        clean = " ".join(str(name or "").split())[:60] or "Celular"
        conn = self._connect()
        try:
            with transaction(conn):
                conn.execute("INSERT INTO devices (name, token_hash, created_at, last_seen, last_ip) VALUES (?, ?, ?, ?, ?)",
                             (clean, hash_token(token), self._now(), self._now(), ip))
                log_audit(conn, "device.paired", {"name": clean, "ip": ip})
        finally:
            conn.close()
        return token

    # --- conferência a cada pedido ------------------------------------------------------------------------------
    def device_for(self, token: str | None, ip: str) -> dict[str, Any] | None:
        if not token or len(token) > 200:
            return None
        conn = self._connect()
        try:
            row = conn.execute("SELECT id, name FROM devices WHERE token_hash = ? AND revoked_at IS NULL",
                               (hash_token(token),)).fetchone()
            if row is None:
                return None
            if self._clock() - self._seen.get(row["id"], 0) > 60:     # atualiza "visto por último" no máximo 1x/min
                self._seen[row["id"]] = self._clock()
                with transaction(conn):
                    conn.execute("UPDATE devices SET last_seen = ?, last_ip = ? WHERE id = ?", (self._now(), ip, row["id"]))
            return dict(row)
        finally:
            conn.close()

    def devices(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT id, name, created_at, last_seen, last_ip FROM devices WHERE revoked_at IS NULL ORDER BY id DESC")]
        finally:
            conn.close()

    def revoke(self, device_id: int) -> bool:
        conn = self._connect()
        try:
            with transaction(conn):
                ok = conn.execute("UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                                  (self._now(), device_id)).rowcount > 0
                if ok:
                    log_audit(conn, "device.revoked", {"device_id": device_id})
            return ok
        finally:
            conn.close()
