"""Provider para o Ollama (API HTTP local), usando apenas a biblioteca padrão."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
import time
from typing import Any, Iterator
from urllib.parse import urlparse

from app.models.base import (
    ChatMessage,
    LLMProvider,
    LLMResponse,
    ModelError,
    ModelUnavailableError,
    ProviderStatus,
    StreamEvent,
)

log = logging.getLogger("nym.models.ollama")

THINK_OPEN, THINK_CLOSE = "<think>", "</think>"


def strip_thinking(text: str) -> str:
    """Remove um bloco <think>...</think> no começo da resposta. Modelos com modo de pensar (ex.: Qwen) às vezes
    soltam o raciocínio no texto mesmo com think=false; isso não é fala do Nym e quebraria o JSON."""
    body = text.lstrip()
    if body.startswith(THINK_OPEN):
        end = body.find(THINK_CLOSE)
        return body[end + len(THINK_CLOSE):].lstrip() if end >= 0 else ""
    return text


class _ThinkFilter:
    """O mesmo, para streaming: segura o começo até saber se é um bloco de pensamento."""

    def __init__(self) -> None:
        self._buf = ""
        self._state = "start"   # start → (thinking →) talking

    def feed(self, chunk: str) -> str:
        if self._state == "talking":
            return chunk
        self._buf += chunk
        if self._state == "start":
            head = self._buf.lstrip()
            if not head or (THINK_OPEN.startswith(head) and len(head) < len(THINK_OPEN)):
                return ""                       # ainda não dá para saber
            if not head.startswith(THINK_OPEN):
                self._state, out, self._buf = "talking", self._buf, ""
                return out
            self._state = "thinking"
        end = self._buf.find(THINK_CLOSE)
        if end < 0:
            return ""
        out = self._buf[end + len(THINK_CLOSE):].lstrip()
        self._state, self._buf = "talking", ""
        return out

    def flush(self) -> str:
        """Fim da resposta: o que sobrou no começo e não era pensamento."""
        out = self._buf if self._state == "start" else ""
        self._buf = ""
        return out


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        temperature: float | None = None,
        num_predict: int | None = None,
        num_ctx: int | None = None,
        keep_alive: str | None = None,
        think: bool | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"URL do Ollama inválida: {base_url!r}")
        if not model.strip():
            raise ValueError("O nome do modelo não pode ser vazio")
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout = timeout
        self.options: dict[str, Any] = {}
        if temperature is not None:
            self.options["temperature"] = temperature
        if num_predict:  # 0 ou None = sem limite
            self.options["num_predict"] = num_predict
        if num_ctx:  # 0 ou None = padrão do Ollama
            self.options["num_ctx"] = num_ctx
        # Mantém o modelo carregado na GPU entre mensagens (o padrão do Ollama descarrega após 5 min).
        self.keep_alive = keep_alive or None
        # Modelos com "modo de pensar" (Gemma 4, Qwen 3) geram raciocínio oculto antes de responder: mais lento.
        # None = não envia (padrão do modelo). Se o modelo recusar o parâmetro, ele é desligado sozinho.
        self.think = think
        self._capabilities: list[str] | None = None   # do /api/show; None = ainda não perguntado

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:  # noqa: S310
                raw = resp.read()
        except urllib.error.HTTPError as exc:  # precisa vir antes de URLError/OSError
            detail = self._error_detail(exc)
            if exc.code == 404:
                raise ModelUnavailableError(
                    f"Modelo '{self.model}' não encontrado no Ollama ({detail}). "
                    f"Instale com: ollama pull {self.model}"
                ) from exc
            raise ModelError(f"Ollama respondeu HTTP {exc.code}: {detail}") from exc
        except TimeoutError as exc:
            raise ModelUnavailableError("Tempo esgotado aguardando o Ollama.") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ModelUnavailableError(
                f"Não foi possível conectar ao Ollama em {self.base_url}. "
                "Verifique se ele está em execução."
            ) from exc
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ModelError("Resposta do Ollama não é um JSON válido.") from exc
        if not isinstance(body, dict):
            raise ModelError("Resposta do Ollama em formato inesperado.")
        return body

    @staticmethod
    def _error_detail(exc: urllib.error.HTTPError) -> str:
        try:
            body = json.loads(exc.read())
            if isinstance(body, dict) and isinstance(body.get("error"), str):
                return body["error"]
        except Exception:  # noqa: BLE001 - só para montar a mensagem
            pass
        return exc.reason if isinstance(exc.reason, str) else "sem detalhes"

    def _payload(self, messages: list[ChatMessage], temperature: float | None, json_mode: bool, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content, **({"images": list(m.images)} if m.images else {})}
                         for m in messages],
            "stream": stream,
        }
        options = dict(self.options)
        if temperature is not None:
            options["temperature"] = temperature
        if options:
            payload["options"] = options
        if json_mode:
            payload["format"] = "json"
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        if self.think is not None:
            payload["think"] = self.think
        return payload

    def _without_think_if_refused(self, exc: ModelError) -> bool:
        """O modelo não aceita o parâmetro "think": desliga e avisa quem chamou para tentar de novo."""
        if self.think is not None and "think" in str(exc).lower():
            log.info("O modelo %s não aceita o parâmetro 'think'; seguindo sem ele.", self.model)
            self.think = None
            return True
        return False

    @staticmethod
    def _stats(body: dict[str, Any], started: float) -> dict[str, Any]:
        ns = 1_000_000_000
        stats: dict[str, Any] = {"wall_s": round(time.monotonic() - started, 3)}
        for key in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration"):
            if isinstance(body.get(key), (int, float)):
                stats[key.replace("_duration", "_s")] = round(body[key] / ns, 3)
        for key in ("prompt_eval_count", "eval_count"):
            if isinstance(body.get(key), int):
                stats[key] = body[key]
        if stats.get("eval_count") and stats.get("eval_s"):
            stats["tokens_per_s"] = round(stats["eval_count"] / stats["eval_s"], 1)
        return stats

    def chat(
        self, messages: list[ChatMessage], *, temperature: float | None = None, json_mode: bool = False
    ) -> LLMResponse:
        started = time.monotonic()
        try:
            body = self._request("POST", "/api/chat", self._payload(messages, temperature, json_mode, False))
        except ModelUnavailableError:
            raise
        except ModelError as exc:
            if not self._without_think_if_refused(exc):
                raise
            body = self._request("POST", "/api/chat", self._payload(messages, temperature, json_mode, False))
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            content = strip_thinking(content)
        if not isinstance(content, str) or not content.strip():
            raise ModelError("O Ollama devolveu uma resposta vazia ou sem conteúdo.")
        return LLMResponse(content=content, model=self.model, stats=self._stats(body, started))

    def stream_chat(self, messages: list[ChatMessage], *, temperature: float | None = None) -> Iterator[StreamEvent]:
        """Pedaços da resposta conforme o Ollama gera. Fechar o gerador fecha a conexão, e o Ollama para de gerar."""
        started = time.monotonic()
        try:
            resp = self._open_stream(self._payload(messages, temperature, False, True))
        except ModelUnavailableError:
            raise
        except ModelError as exc:
            if not self._without_think_if_refused(exc):
                raise
            resp = self._open_stream(self._payload(messages, temperature, False, True))
        got_text = False
        thinking = _ThinkFilter()
        try:
            for raw in resp:
                line = raw.strip()
                if not line:
                    continue
                try:
                    body = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ModelError("Pedaço de resposta do Ollama inválido.") from exc
                if isinstance(body.get("error"), str):
                    raise ModelError(f"Ollama: {body['error']}")
                message = body.get("message")
                text = message.get("content") if isinstance(message, dict) else ""
                text = thinking.feed(text) if isinstance(text, str) and text else ""
                if body.get("done"):
                    text += thinking.flush()
                if text:
                    got_text = got_text or bool(text.strip())
                    yield StreamEvent(text=text)
                if body.get("done"):
                    if not got_text:
                        raise ModelError("O Ollama devolveu uma resposta vazia ou sem conteúdo.")
                    yield StreamEvent(done=True, stats=self._stats(body, started))
                    return
            raise ModelError("A conexão com o Ollama terminou antes do fim da resposta.")
        except TimeoutError as exc:
            raise ModelUnavailableError("Tempo esgotado aguardando o Ollama.") from exc
        except OSError as exc:
            raise ModelUnavailableError("A conexão com o Ollama caiu no meio da resposta.") from exc
        finally:
            resp.close()

    def _open_stream(self, payload: dict[str, Any]):
        req = urllib.request.Request(f"{self.base_url}/api/chat", data=json.dumps(payload).encode("utf-8"),
                                     method="POST", headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)  # noqa: S310
        except urllib.error.HTTPError as exc:
            detail = self._error_detail(exc)
            if exc.code == 404:
                raise ModelUnavailableError(
                    f"Modelo '{self.model}' não encontrado no Ollama ({detail}). Instale com: ollama pull {self.model}") from exc
            raise ModelError(f"Ollama respondeu HTTP {exc.code}: {detail}") from exc
        except TimeoutError as exc:
            raise ModelUnavailableError("Tempo esgotado aguardando o Ollama.") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ModelUnavailableError(
                f"Não foi possível conectar ao Ollama em {self.base_url}. Verifique se ele está em execução.") from exc

    def capabilities(self, refresh: bool = False) -> list[str]:
        """O que o modelo sabe fazer, segundo o Ollama (ex.: completion, vision). Lista vazia se não der para saber."""
        if self._capabilities is not None and not refresh:
            return self._capabilities
        try:
            body = self._request("POST", "/api/show", {"model": self.model}, timeout=min(self.timeout, 10.0))
        except ModelError as exc:
            log.info("Não deu para consultar as capacidades de %s: %s", self.model, exc)
            return []                                   # não guarda: tenta de novo da próxima vez
        caps = body.get("capabilities")
        self._capabilities = [str(c) for c in caps] if isinstance(caps, list) else []
        return self._capabilities

    def supports_vision(self) -> bool:
        return "vision" in self.capabilities()

    def warm_up(self) -> None:
        """Pede ao Ollama para carregar o modelo agora (mensagem vazia), para a primeira resposta não esperar o carregamento."""
        payload: dict[str, Any] = {"model": self.model, "messages": []}
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        try:
            self._request("POST", "/api/chat", payload, timeout=max(self.timeout, 60.0))
            log.info("Modelo %s carregado e pronto.", self.model)
        except ModelError as exc:
            log.info("Não foi possível pré-carregar o modelo: %s", exc)

    def check_availability(self) -> ProviderStatus:
        try:
            body = self._request("GET", "/api/tags", timeout=min(self.timeout, 3.0))
        except ModelError as exc:
            return ProviderStatus(False, str(exc))
        models = body.get("models")
        names = [m.get("name", "") for m in models if isinstance(m, dict)] if isinstance(models, list) else []
        wanted = self.model
        found = any(n == wanted or n.split(":")[0] == wanted or n == f"{wanted}:latest" for n in names)
        if found:
            return ProviderStatus(True, f"Modelo '{wanted}' disponível.")
        return ProviderStatus(
            False, f"Ollama ativo, mas o modelo '{wanted}' não está instalado (ollama pull {wanted})."
        )
