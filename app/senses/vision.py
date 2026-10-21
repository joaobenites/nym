"""Visão do Nym (Fase 8 — sentidos).

O que é real: uma imagem (que você mandou, um quadro da câmera ou da tela quando VOCÊ clica, uma imagem de pasta
autorizada ou um quadro de vídeo) vai para um modelo com visão no Ollama, que descreve o que aparece. A descrição
fica registrada em `perceptions` com a origem; é ela que o Nym "lembra" de ter visto. A imagem em si só é guardada
se você ligar NYM_KEEP_IMAGES.

Regras fixas:
- Câmera e tela: só quando o usuário clica na interface. Não existe ferramenta nem rotina que ligue a câmera.
- A descrição não identifica pessoas pelo nome nem tira conclusões sensíveis (saúde, origem, religião...).
- Imagem conferida pelo conteúdo (assinatura de JPEG/PNG/WebP/GIF) e pelo tamanho antes de ir ao modelo.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable, Sequence

from app.database.connection import connect, transaction
from app.database.repository import log_audit, utcnow
from app.models.base import ChatMessage, LLMProvider, ModelError

log = logging.getLogger("nym.senses")

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES_PER_MESSAGE = 3
MAX_DESCRIPTION_CHARS = 1500
KINDS = ("image", "camera", "screen", "file", "video")
LABEL = {"image": "imagem que ele mandou", "camera": "câmera dele", "screen": "tela dele", "file": "imagem de pasta",
         "video": "vídeo"}
WHERE = {
    "image": "uma imagem que o usuário te mandou no chat",
    "camera": "um quadro da câmera do computador do usuário, agora (ele clicou para te mostrar)",
    "screen": "um quadro da tela do computador do usuário, agora (ele clicou para te mostrar o que está vendo)",
    "file": "uma imagem de uma pasta que ele autorizou você a ler",
    "video": "um quadro de um vídeo que você está assistindo",
}

PROMPT = """Você é os olhos do Nym. Descreva em português, de forma objetiva e concreta, o que aparece na imagem:
o principal primeiro, depois detalhes que chamam atenção (objetos, cores, ambiente, ação, clima). Se houver texto
legível, transcreva o que importa. Se for uma tela de computador, diga qual programa/jogo/site parece ser e o que
está acontecendo nela.
Regras: descreva só o que dá para ver; se algo estiver incerto, diga "parece". Pessoas: descreva aparência geral,
expressão e o que fazem, sem dizer quem são e sem supor saúde, origem, religião ou coisas do tipo.
No máximo 8 frases. Sem introdução ("A imagem mostra..." pode)."""

_SIGNATURES = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"GIF87a", "image/gif"),
               (b"GIF89a", "image/gif"))


class SenseError(ValueError):
    """Entrada inválida (imagem estranha, sentido indisponível...)."""


def image_type(raw: bytes) -> str | None:
    for sig, mime in _SIGNATURES:
        if raw.startswith(sig):
            return mime
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def decode_image(data: str) -> tuple[str, bytes, str]:
    """Aceita base64 puro ou data URL. Devolve (base64 limpo, bytes, tipo). Levanta SenseError."""
    if not isinstance(data, str) or not data.strip():
        raise SenseError("Imagem vazia.")
    text = data.strip()
    m = re.match(r"^data:([\w/+.-]+);base64,(.*)$", text, re.S)
    if m:
        text = m.group(2)
    if len(text) > MAX_IMAGE_BYTES * 4 // 3 + 16:
        raise SenseError("Imagem grande demais (máximo 8 MB).")
    try:
        raw = base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError):
        raise SenseError("Imagem em formato inválido.") from None
    if len(raw) > MAX_IMAGE_BYTES:
        raise SenseError("Imagem grande demais (máximo 8 MB).")
    mime = image_type(raw)
    if mime is None:
        raise SenseError("Não é uma imagem JPEG, PNG, WebP ou GIF.")
    return base64.b64encode(raw).decode("ascii"), raw, mime


class VisionService:
    def __init__(self, settings: Any, provider: LLMProvider, make_provider: Callable[[str], LLMProvider] | None = None,
                 clock: Callable[[], Any] | None = None) -> None:
        self._settings = settings
        self._main = provider
        self._make = make_provider
        self._vision: LLMProvider | None = None
        self._clock = clock

    def provider(self) -> LLMProvider:
        name = (getattr(self._settings, "vision_model", "") or "").strip()
        if not name or name == self._main.model or self._make is None:
            return self._main
        if self._vision is None:
            self._vision = self._make(name)
        return self._vision

    def status(self) -> dict[str, Any]:
        p = self.provider()
        try:
            ok = p.supports_vision()
        except Exception:  # noqa: BLE001 - status nunca derruba nada
            ok = False
        detail = ("pronta" if ok else
                  f"o modelo {p.model} não enxerga imagens (ou o Ollama está fora). Use um modelo com visão "
                  "(o Gemma 4 enxerga) ou defina NYM_VISION_MODEL.")
        return {"available": ok, "model": p.model, "detail": detail, "keep_images": bool(self._settings.keep_images)}

    def _now(self) -> str:
        return self._clock().isoformat(timespec="milliseconds") if self._clock else utcnow()

    def describe(self, image_b64: str, kind: str, hint: str = "") -> str:
        """Chama o modelo de visão. Levanta ModelError (indisponível/erro) ou SenseError (sem visão)."""
        p = self.provider()
        if not p.supports_vision():
            raise SenseError(f"O modelo {p.model} não enxerga imagens.")
        user = f"Isto é {WHERE.get(kind, 'uma imagem')}."
        if hint:
            user += f" Contexto (pode ajudar a focar, mas descreva o que se vê): {hint[:400]}"
        reply = p.chat([ChatMessage("system", PROMPT), ChatMessage("user", user, images=(image_b64,))], temperature=0.2)
        text = " ".join(reply.content.split())
        if not text:
            raise ModelError("O modelo de visão não descreveu nada.")
        return text[:MAX_DESCRIPTION_CHARS]

    def perceive(self, kind: str, source: str, image_b64: str, raw: bytes | None = None, hint: str = "",
                 message_id: int | None = None, goal_id: int | None = None, title: str | None = None,
                 details: dict[str, Any] | None = None) -> dict[str, Any]:
        """Descreve e registra. Se a visão falhar, registra a falha (o Nym precisa saber que NÃO viu)."""
        if kind not in KINDS:
            raise SenseError("Tipo de percepção inválido.")
        info = dict(details or {})
        info["model"] = self.provider().model
        try:
            description = self.describe(image_b64, kind, hint)
        except (ModelError, SenseError) as exc:
            log.warning("Visão falhou (%s): %s", kind, exc)
            description = f"Não consegui enxergar esta imagem ({exc})."
            info["failed"] = True
        conn = connect(self._settings.db_path)
        try:
            with transaction(conn):
                pid = store(conn, kind, source, description, self._now(), title=title, details=info,
                            message_id=message_id, goal_id=goal_id)
                if self._settings.keep_images and raw is not None and not info.get("failed"):
                    folder = Path(self._settings.data_dir) / "perceptions"
                    folder.mkdir(parents=True, exist_ok=True)
                    ext = {"image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}.get(image_type(raw) or "", ".jpg")
                    path = folder / f"{pid}{ext}"
                    path.write_bytes(raw)
                    conn.execute("UPDATE perceptions SET image_path = ? WHERE id = ?", (str(path), pid))
                log_audit(conn, "senses.perceived", {"perception_id": pid, "kind": kind, "failed": bool(info.get("failed"))})
        finally:
            conn.close()
        return {"id": pid, "kind": kind, "source": source, "description": description, "failed": bool(info.get("failed"))}


# --- registro --------------------------------------------------------------------------------------------------
def store(conn: sqlite3.Connection, kind: str, source: str, description: str, now: str, title: str | None = None,
          details: dict[str, Any] | None = None, message_id: int | None = None, goal_id: int | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO perceptions (kind, source, title, description, details, message_id, goal_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (kind, source[:500], (title or None) and title[:300], description[:20000],
         json.dumps(details or {}, ensure_ascii=False), message_id, goal_id, now))
    return int(cur.lastrowid)


def for_messages(conn: sqlite3.Connection, ids: Sequence[int]) -> dict[int, list[dict[str, Any]]]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    out: dict[int, list[dict[str, Any]]] = {}
    for r in conn.execute(f"SELECT id, kind, source, description, message_id FROM perceptions "  # noqa: S608
                          f"WHERE message_id IN ({marks}) ORDER BY id", list(ids)):
        out.setdefault(r["message_id"], []).append(dict(r))
    return out


def history_note(items: Sequence[dict[str, Any]]) -> str:
    """Texto colado na fala do usuário no histórico: o que o Nym viu junto daquela mensagem."""
    return "\n".join(f"[O que você viu junto desta mensagem ({LABEL.get(p['kind'], p['kind'])}): {p['description']}]"
                     for p in items)


def recent_videos_block(conn: sqlite3.Connection, limit: int = 3) -> str:
    rows = conn.execute("SELECT title, source, description, created_at FROM perceptions WHERE kind = 'video' "
                        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    if not rows:
        return ""
    lines = [f"- \"{r['title'] or r['source']}\" ({r['created_at'][:10]}): {r['description'][:400]}" for r in rows]
    return "Vídeos que você assistiu de verdade (o que está aqui é o que você sabe deles):\n" + "\n".join(lines)


def listing(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    out = []
    for r in conn.execute("SELECT * FROM perceptions ORDER BY id DESC LIMIT ?", (limit,)):
        d = dict(r)
        d["details"] = json.loads(d["details"] or "{}")
        d["has_image"] = bool(d.pop("image_path"))
        out.append(d)
    return out


def delete(conn: sqlite3.Connection, perception_id: int) -> bool:
    row = conn.execute("SELECT image_path FROM perceptions WHERE id = ?", (perception_id,)).fetchone()
    if row is None:
        return False
    if row["image_path"]:
        try:
            Path(row["image_path"]).unlink(missing_ok=True)
        except OSError:
            log.warning("Não deu para apagar a imagem da percepção %s.", perception_id)
    conn.execute("DELETE FROM perceptions WHERE id = ?", (perception_id,))
    return True
