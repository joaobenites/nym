"""Serviço de conversa: um único chat contínuo com Nym.

Persiste mensagens, monta o contexto e chama o modelo. A chamada ao modelo acontece
FORA de qualquer transação, para não segurar o banco durante uma inferência longa. A
mensagem do usuário é gravada antes da chamada: se o modelo falhar, nada do que o
usuário escreveu se perde.

Velocidade (Fase 6): o prompt de sistema tem só a parte FIXA (identidade, voz, regras) e o histórico vem
logo depois, sem nada que mude a cada mensagem no meio. Assim o Ollama reaproveita o que já processou e só
calcula o fim. O que muda a cada mensagem (hora, estado, lembranças, sonhos, tarefas) vai num bloco marcado
junto da ÚLTIMA fala do usuário. A resposta pode ser enviada em pedaços (`stream_message`).
"""
from __future__ import annotations

import json
import re
import sqlite3
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Callable, Iterator, Sequence

from app.config import Settings
from app.core.activity import ActivityGate
from app.core.identity import IdentityService
from app.core.state import StateService
from app.core.timesense import gap_marker, time_block
from app.core import commitments, followups, pet, rhythm, speaker, tastes
from app.core import requests as requests_mod
from app.creations import store as creation_store
from app.learning import feedback, lessons
from app.senses import vision
from app.life import day as day_mod
from app.life import projects as projects_mod
from app.core.voice import HONESTY_ISSUES, find_issues, remove_emoji, retry_note, strip_emoji
from app.database import repository as repo
from app.database.connection import connect, transaction
from app.dreams.journal import DreamJournal
from app.memory.service import MemoryService
from app.memory.text import normalize, token_set
from app.models.base import ChatMessage, LLMProvider, LLMResponse, ModelError, ModelUnavailableError

log = logging.getLogger("nym.chat")

MAX_MESSAGE_CHARS = 8000
MAIN_CONVERSATION_KEY = "main_conversation_id"
MAIN_TITLE = "Conversa com Nym"
CONTEXT_OPEN = "[Contexto deste momento: registro do sistema, não é fala dele. Use se ajudar; não comente o bloco.]"
CONTEXT_CLOSE = "[Fim do contexto. A mensagem dele vem a seguir.]"
DEFAULT_HISTORY_LIMIT = 100
MAX_HISTORY_LIMIT = 500
AWAY_SECONDS = 3 * 3600   # Fase 9: depois de tanto tempo sem conversa, ele recebe o que fez enquanto isso


_TODAY_RE = re.compile(r"\b(o que (voce )?(fez|andou fazendo|tem feito|esteve fazendo|ficou fazendo)|fez (hoje|ontem|sozinho|algo|alguma coisa)|"
                       r"(hoje|ontem) (voce )?(fez|ficou)|no que (voce )?(trabalhou|mexeu)|o que rolou (hoje|ai))\b")


def asks_about_today(text: str) -> bool:
    """O dono perguntou o que ele fez/andou fazendo: a resposta deve sair do dia registrado, não da imaginação."""
    return bool(_TODAY_RE.search(normalize(text)))


class ChatError(Exception):
    pass


class InvalidInputError(ChatError):
    pass


class ChatFailure(ChatError):
    """O modelo falhou, mas a mensagem do usuário foi preservada."""

    def __init__(self, message: str, user_message_id: int, unavailable: bool):
        super().__init__(message)
        self.user_message_id = user_message_id
        self.unavailable = unavailable


@dataclass(frozen=True)
class ChatResult:
    user_message_id: int
    assistant_message_id: int
    reply: str
    model: str
    memories_used: list[int] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)   # objetivos que começaram por causa desta resposta


class ChatService:
    def __init__(
        self,
        settings: Settings,
        provider: LLMProvider,
        identity: IdentityService | None = None,
        memory: MemoryService | None = None,
        gate: ActivityGate | None = None,
        state: StateService | None = None,
        dreams: DreamJournal | None = None,
        clock: Callable[[], datetime] | None = None,
        tz: tzinfo | None = None,
        tasks=None,
        senses=None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._identity = identity or IdentityService(settings)
        self._memory = memory
        self._gate = gate or ActivityGate()
        self._state = state
        self._dreams = dreams
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._tz = tz  # None = fuso do computador
        self._tasks = tasks  # TaskManager (Fase 5), para o Nym saber o que está fazendo de verdade
        self._stop = threading.Event()  # "Interromper" no meio de uma resposta em streaming
        self._senses = senses  # VisionService (Fase 8): o que ele vê junto das mensagens
        self._auto_goals: list[dict] = []   # objetivos criados pelo CÓDIGO a partir do pedido desta fala (Fase 10)
        self._pending_progress = False      # ele cobrou um pedido que ainda não tem resultado (Fase 10.1)
        self._recent_replies: list[str] = []   # as últimas falas do próprio Nym, para o ritmo (Fase 10.1)

    @staticmethod
    def _main_conversation(conn) -> str | None:
        cid = repo.get_state(conn, MAIN_CONVERSATION_KEY)
        if cid and repo.get_conversation(conn, cid) is not None:
            return cid
        return None

    def send_message(self, text: str, images: Sequence[dict[str, Any]] | None = None) -> ChatResult:
        with self._gate.chat():
            return self._send_message(text, images)

    def _send_message(self, text: str, images: Sequence[dict[str, Any]] | None = None) -> ChatResult:
        text = self._check_text(text)
        seen = self._check_images(images)

        conn = connect(self._settings.db_path)
        try:
            cid, user_id, context, memories_used = self._begin(conn, text, seen)
            try:
                response, voice = self._reply(context, text)
            except ModelError as exc:
                log.warning("Falha do modelo: %s", exc)
                repo.log_audit(
                    conn,
                    "chat.model_failure",
                    {"user_message_id": user_id, "model": self._provider.model, "error": str(exc)},
                )
                raise ChatFailure(
                    str(exc), user_id, unavailable=isinstance(exc, ModelUnavailableError)
                ) from exc

            raw = response.content
            reply = commitments.visible_text(raw, final=True) or raw.strip()
            with transaction(conn):
                assistant_id = repo.add_message(
                    conn, cid, "assistant", reply, model=response.model,
                    created_at=self._clock().isoformat(timespec="milliseconds"),
                )
                repo.log_audit(
                    conn,
                    "chat.reply",
                    {"model": response.model, "context_messages": len(context),
                     "assistant_message_id": assistant_id, "memory_ids": memories_used, "voice": voice},
                )
            actions = self._act_on_commitments(raw, reply, text, assistant_id)
            self._mark_followups(conn, reply)
            self._mark_lessons(conn, assistant_id)
            return ChatResult(user_id, assistant_id, reply, response.model, memories_used, actions)
        finally:
            conn.close()

    @staticmethod
    def _check_text(text: str) -> str:
        text = (text or "").strip()
        if not text:
            raise InvalidInputError("A mensagem não pode estar vazia.")
        if len(text) > MAX_MESSAGE_CHARS:
            raise InvalidInputError(f"A mensagem excede {MAX_MESSAGE_CHARS} caracteres.")
        return text

    def _check_images(self, images: Sequence[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Confere as imagens ANTES de gravar qualquer coisa (Fase 8). Devolve [{kind, b64, raw, name}]."""
        if not images:
            return []
        if not isinstance(images, (list, tuple)) or len(images) > vision.MAX_IMAGES_PER_MESSAGE:
            raise InvalidInputError(f"No máximo {vision.MAX_IMAGES_PER_MESSAGE} imagens por mensagem.")
        if self._senses is None or not self._senses.status()["available"]:
            detail = self._senses.status()["detail"] if self._senses is not None else "a visão não está ligada"
            raise InvalidInputError(f"Ele ainda não consegue ver imagens: {detail}")
        out = []
        for img in images:
            if not isinstance(img, dict):
                raise InvalidInputError("Imagem mal formada.")
            kind = img.get("kind") or "image"
            if kind not in ("image", "camera", "screen"):
                raise InvalidInputError("Origem da imagem inválida.")
            try:
                b64, raw, _mime = vision.decode_image(img.get("data", ""))
            except vision.SenseError as exc:
                raise InvalidInputError(str(exc)) from None
            name = str(img.get("name") or {"camera": "câmera", "screen": "tela"}.get(kind, "imagem"))[:120]
            out.append({"kind": kind, "b64": b64, "raw": raw, "name": name})
        return out

    def _save_user(self, conn, text: str) -> tuple[str, int, datetime]:
        with transaction(conn):
            cid = self._main_conversation(conn)
            if cid is None:
                cid = repo.create_conversation(conn, MAIN_TITLE)
                repo.set_state(conn, MAIN_CONVERSATION_KEY, cid)
            now = self._clock()
            user_id = repo.add_message(conn, cid, "user", text, created_at=now.isoformat(timespec="milliseconds"))
        return cid, user_id, now

    def _perceive(self, user_id: int, text: str, seen: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Olha cada imagem (modelo de visão) e registra o que viu, ligado à fala do usuário."""
        return [self._senses.perceive(img["kind"], img["name"], img["b64"], img["raw"], hint=text, message_id=user_id)
                for img in seen]

    def _begin(self, conn, text: str, seen: Sequence[dict[str, Any]] = ()) -> tuple[str, int, list[ChatMessage], list[int]]:
        """Grava a fala do usuário e monta o contexto. Devolve (conversa, id da fala, contexto, memórias usadas)."""
        cid, user_id, now = self._save_user(conn, text)
        if seen:
            self._perceive(user_id, text, seen)
        context, used = self._build_context(conn, cid, now)
        return cid, user_id, context, used

    # --- streaming -----------------------------------------------------------------------------------
    def stop(self) -> None:
        """Interrompe a resposta em andamento: o Ollama para de gerar e o que já saiu fica salvo."""
        self._stop.set()

    def stream_message(self, text: str, images: Sequence[dict[str, Any]] | None = None) -> Iterator[dict[str, Any]]:
        """Eventos: start, seeing/seen (quando há imagem), delta (pedaço do texto), done (resposta salva) ou error / stopped.

        A validação acontece ANTES do primeiro evento (InvalidInputError sobe para quem chamou)."""
        text = self._check_text(text)
        seen = self._check_images(images)
        return self._stream(text, seen)

    def _stream(self, text: str, seen: Sequence[dict[str, Any]] = ()) -> Iterator[dict[str, Any]]:
        started = time.monotonic()
        with self._gate.chat():
            self._stop.clear()
            conn = connect(self._settings.db_path)
            stream = None
            parts: list[str] = []
            finished = False
            try:
                if seen:
                    cid, user_id, now = self._save_user(conn, text)
                    yield {"type": "seeing", "user_message_id": user_id, "count": len(seen)}
                    perceived = self._perceive(user_id, text, seen)
                    yield {"type": "seen", "perceptions": [{k: p[k] for k in ("id", "kind", "description", "failed")}
                                                           for p in perceived]}
                    context, used = self._build_context(conn, cid, now)
                else:
                    cid, user_id, context, used = self._begin(conn, text)
                yield {"type": "start", "user_message_id": user_id, "memories_used": used}
                stats: dict[str, Any] = {}
                shown, sent_raw = 0, ""
                try:
                    stream = self._provider.stream_chat(context)
                    for event in stream:
                        if self._stop.is_set():
                            break
                        if event.done:
                            stats = event.stats
                            break
                        parts.append(event.text)
                        visible = commitments.visible_text("".join(parts))   # a marca [[fazer: ...]] nunca aparece
                        if len(visible) > shown and visible.startswith(sent_raw):
                            piece = visible[shown:]
                            sent_raw, shown = visible, len(visible)
                            piece = piece if self._settings.allow_emoji else remove_emoji(piece)
                            if piece:
                                yield {"type": "delta", "text": piece}
                except ModelError as exc:
                    log.warning("Falha do modelo (streaming): %s", exc)
                    repo.log_audit(conn, "chat.model_failure",
                                   {"user_message_id": user_id, "model": self._provider.model, "error": str(exc)})
                    if parts:  # o que já tinha saído não se perde
                        self._save_reply(conn, cid, "".join(parts), context, used, text, {}, started, partial=True)
                    finished = True
                    yield {"type": "error", "detail": str(exc), "user_message_id": user_id,
                           "retryable": isinstance(exc, ModelUnavailableError)}
                    return
                stopped = self._stop.is_set()
                if stopped and not parts:
                    finished = True
                    yield {"type": "stopped", "user_message_id": user_id}
                    return
                result = self._save_reply(conn, cid, "".join(parts), context, used, text, stats, started, partial=stopped)
                finished = True
                yield {"type": "stopped" if stopped else "done", **result}
                if not stopped and self._settings.voice_retry:
                    fix = self._correct(conn, cid, context, result, text)   # Fase 10: ele percebe que falou sem checar
                    if fix:
                        yield {"type": "correction", **fix}
            finally:
                if stream is not None:
                    stream.close()          # fecha a conexão: o Ollama para de gerar
                if not finished and parts:  # o navegador fechou no meio: guarda o que já tinha saído
                    try:
                        self._save_reply(conn, cid, "".join(parts), context, used, text, {}, started, partial=True)
                    except Exception:  # noqa: BLE001
                        log.exception("Não foi possível salvar a resposta interrompida.")
                conn.close()

    def _save_reply(self, conn, cid: str, raw: str, context, used: list[int], user_text: str, stats: dict[str, Any],
                    started: float, partial: bool = False) -> dict[str, Any]:
        clean = commitments.visible_text(raw, final=True) or raw.strip()
        reply = clean if self._settings.allow_emoji else (strip_emoji(clean) or clean.strip())
        voice = {"issues": self._issues(user_text, reply, context),
                 "retried": False, "streamed": True}
        timing = {**stats, "server_s": round(time.monotonic() - started, 3)}
        with transaction(conn):
            assistant_id = repo.add_message(conn, cid, "assistant", reply, model=self._provider.model,
                                            created_at=self._clock().isoformat(timespec="milliseconds"))
            repo.log_audit(conn, "chat.reply", {"model": self._provider.model, "context_messages": len(context),
                                                "assistant_message_id": assistant_id, "memory_ids": used, "voice": voice,
                                                "timing": timing, "partial": partial})
        log.info("Resposta em %.1fs (%s)", timing["server_s"],
                 ", ".join(f"{k}={v}" for k, v in stats.items() if k in ("load_s", "prompt_eval_s", "eval_s", "tokens_per_s")))
        actions = [] if partial else self._act_on_commitments(raw, reply, user_text, assistant_id)
        self._mark_followups(conn, reply)
        self._mark_lessons(conn, assistant_id)
        return {"assistant_message_id": assistant_id, "reply": reply, "model": self._provider.model,
                "memories_used": used, "timing": timing, "partial": partial, "actions": actions,
                "issues": voice["issues"]}

    def _correct(self, conn, cid: str, context: list[ChatMessage], result: dict[str, Any], user_text: str) -> dict[str, Any] | None:
        """Streaming não reescreve o que já saiu. Se a resposta afirmou algo sem registro (ação que ele não fez, lembrança
        que ele não tem), o Nym manda logo em seguida uma mensagem curta se corrigindo, como um amigo que percebe que
        falou sem checar. Só para problemas de HONESTIDADE; o modelo falhar aqui nunca derruba a conversa."""
        problems = [i for i in result.get("issues", []) if i in HONESTY_ISSUES]
        if not problems:
            return None
        note = ("[Registro do sistema, não é fala dele: na sua última resposta você " + retry_note(problems).split("porque ", 1)[-1]
                .split(". Responda de novo", 1)[0] + ". Escreva só a mensagem curta (1 a 2 frases) que você mandaria em "
                "seguida para se corrigir, do seu jeito de amigo: diga o que de fato não aconteceu ou não está registrado, "
                "sem drama e sem pedir desculpa demais, e, se for um trabalho que ainda vai fazer, que conta quando tiver.]")
        try:
            msgs = [*context, ChatMessage("assistant", result["reply"]), ChatMessage("user", note)]
            raw = self._provider.chat(msgs, temperature=0.4).content
        except ModelError as exc:
            log.info("Correção não saiu (%s).", exc)
            return None
        text = commitments.visible_text(raw, final=True).strip().strip('"“”').strip()
        text = text if self._settings.allow_emoji else strip_emoji(text)
        if len(text) < 8 or [i for i in self._issues(user_text, text, context) if i in HONESTY_ISSUES]:
            return None            # a correção também escorregou: melhor não falar nada do que corrigir errado
        text = text[:600]
        with transaction(conn):
            mid = repo.add_message(conn, cid, "assistant", text, model=self._provider.model,
                                   created_at=self._clock().isoformat(timespec="milliseconds"))
            repo.log_audit(conn, "chat.correction", {"assistant_message_id": mid, "fixes": result["assistant_message_id"],
                                                     "issues": problems})
        return {"assistant_message_id": mid, "text": text, "fixes": result["assistant_message_id"]}

    def _mark_lessons(self, conn, assistant_id: int) -> None:
        """Lições de conversa que estavam no contexto: a sua reação a esta resposta dá ou tira ponto delas."""
        used = getattr(self, "_chat_lessons", [])
        if not used:
            return
        try:
            with transaction(conn):
                lessons.mark_used(conn, used, "message", assistant_id, self._clock().isoformat(timespec="milliseconds"))
        except Exception:  # noqa: BLE001
            log.exception("Falha ao registrar o uso das lições.")

    def _mark_followups(self, conn, reply: str) -> None:
        """Se a resposta tocou num lembrete que estava no contexto, ele foi perguntado: não repete pela presença."""
        items = getattr(self, "_due_followups", [])
        if not items:
            return
        with transaction(conn):
            for f in items:
                if followups.asked_in(reply, f):
                    followups.mark_delivered(conn, f["id"], self._clock().isoformat(timespec="milliseconds"))
                    conn.execute("UPDATE pending_messages SET status = 'dismissed' WHERE kind = 'followup' AND refs = ? "
                                 "AND status = 'pending'", (json.dumps([f["id"]]),))

    # --- compromissos: "vou fazer" vira objetivo de verdade (ver app/core/commitments.py) -----------------------
    def _act_on_commitments(self, raw: str, reply: str, user_text: str, message_id: int) -> list[dict]:
        actions, self._auto_goals = list(self._auto_goals), []
        if self._tasks is None or not self._settings.chat_commitments:
            return actions
        tags = commitments.extract_tags(raw)
        for description in tags:
            try:
                goal = self._tasks.commit_from_chat(description, message_id, user_text)
            except Exception:  # noqa: BLE001 - registrar o objetivo nunca derruba a conversa
                log.exception("Não foi possível registrar o compromisso da conversa.")
                continue
            if goal:
                actions.append({"goal_id": goal["id"], "description": goal["description"]})
        if not tags and commitments.sounds_like_promise(reply):
            threading.Thread(target=self._check_promise, args=(user_text, reply, message_id),
                             name="nym-commitment-check", daemon=True).start()
        return actions

    def _check_promise(self, user_text: str, reply: str, message_id: int) -> None:
        """Ele prometeu sem a marca: pergunta ao modelo, em segundo plano, se houve compromisso concreto."""
        try:
            with self._gate.work():
                raw = self._provider.chat(commitments.extraction_messages(user_text, reply), temperature=0.1,
                                          json_mode=True).content
            description = commitments.parse_extraction(raw)
            if description:
                goal = self._tasks.commit_from_chat(description, message_id, user_text)
                if goal:
                    log.info("Compromisso da conversa virou o objetivo #%s.", goal["id"])
        except Exception:  # noqa: BLE001
            log.exception("Checagem de compromisso falhou; nada foi criado.")

    def _issues(self, user_text: str, reply: str, context: list[ChatMessage]) -> list[str]:
        """Vícios da resposta (ver voice.find_issues), com o estado desta fala: pedido pendente cobrado e últimas respostas."""
        return find_issues(user_text, reply, self._evidence(context), self._registry(context),
                           pending_progress=self._pending_progress, recent_replies=self._recent_replies)

    @staticmethod
    def _evidence(context: list[ChatMessage]) -> str:
        """O que o Nym realmente tem para sustentar uma lembrança: identidade, falas do USUÁRIO e o bloco de contexto
        (lembranças, sonhos, tarefas, diário). Falas do próprio Nym ficam de fora: uma invenção antiga não prova a nova."""
        return "\n".join(m.content for m in context if m.role in ("system", "user"))

    @staticmethod
    def _registry(context: list[ChatMessage]) -> str:
        """Só o bloco de contexto do momento (tarefas, dia, vídeos, criações, lembranças): o registro que sustenta o
        que o Nym diz ter FEITO. Fica de fora a fala do usuário: pedir "procure X" não prova que ele procurou X."""
        for m in reversed(context):
            if m.role == "user" and CONTEXT_OPEN in m.content:
                inner = m.content.split(CONTEXT_OPEN, 1)[1].split(CONTEXT_CLOSE, 1)[0]
                keep = []
                for line in inner.split("\n"):
                    low = normalize(line)
                    if low.startswith(("pedido dele registrado agora", "situacao do pedido dele", "ritmo desta conversa")):
                        continue   # o próprio pedido ainda sem resultado não prova que ele fez o trabalho
                    if line.lstrip().startswith("- ") and any(k in low for k in (
                            "em andamento", "parado", "ideia sua", "decidiu fazer", "travou em")):
                        continue   # objetivo aberto/decidido não é trabalho feito: só "concluído" e o que aconteceu de fato
                    keep.append(line)
                return "\n".join(keep)
        return ""

    def _reply(self, context: list[ChatMessage], user_text: str) -> tuple[LLMResponse, dict]:
        """Chama o modelo e passa a resposta pelo filtro de voz (ver app/core/voice.py).

        Se a resposta cair num vício de chatbot, pede UMA nova versão. A nova só substitui a primeira se tiver
        menos problemas; se a segunda chamada falhar, fica a primeira (o usuário nunca fica sem resposta por isso)."""
        response = self._provider.chat(context)
        issues = self._issues(user_text, response.content, context)
        voice: dict = {"issues": issues, "retried": False}
        if issues and self._settings.voice_retry:
            note = retry_note(issues)
            retry_ctx = [ChatMessage("system", context[0].content + "\n\n" + note), *context[1:]]
            try:
                second = self._provider.chat(retry_ctx)
            except ModelError as exc:
                log.info("Nova versão da resposta não saiu (%s); fica a primeira.", exc)
            else:
                second_issues = self._issues(user_text, second.content, context)
                voice.update(retried=True, issues_after=second_issues)
                if len(second_issues) < len(issues) and second.content.strip():
                    response = second
        if not self._settings.allow_emoji:
            cleaned = strip_emoji(response.content)
            if cleaned:
                response = LLMResponse(content=cleaned, model=response.model)
        return response, voice

    def _build_context(self, conn, conversation_id: str, now: datetime | None = None) -> tuple[list[ChatMessage], list[int]]:
        kept_rows = self._window(conn, conversation_id)
        self._auto_goals = []
        now = now or self._clock()
        current = kept_rows[-1]
        previous_at = self._previous_at(conn, conversation_id, current["id"])

        # Consulta: a fala atual. Só se ela for curta demais ("e aí?", "sim"), junta as anteriores:
        # misturar tudo sempre dilui a pergunta real. Memórias já presentes no contexto são puladas.
        query = kept_rows[-1]["content"][:500]
        if len(token_set(query)) < 3:
            query = " ".join(r["content"][:300] for r in kept_rows[-3:])
        memory_block, used = "", []
        if self._memory is not None:
            try:
                memory_block, used = self._memory.block_for_chat(query, {r["id"] for r in kept_rows})
            except Exception:  # noqa: BLE001 - a memória é auxiliar: nunca derruba a conversa
                log.exception("Falha ao recuperar memórias; seguindo sem elas.")
        if self._dreams is not None:
            try:
                dream_block = self._dreams.block_for_chat(kept_rows[-1]["content"], since=previous_at, now=now, tz=self._tz)
                if dream_block:
                    memory_block = (memory_block + "\n\n" + dream_block).strip()
            except Exception:  # noqa: BLE001
                log.exception("Falha ao ler o diário de sonhos; seguindo sem ele.")
        try:
            with transaction(conn):
                self._due_followups = followups.due(conn, now)
            fu_block = followups.block_for_chat(self._due_followups)
            if fu_block:
                memory_block = (memory_block + "\n\n" + fu_block).strip()
        except Exception:  # noqa: BLE001
            self._due_followups = []
            log.exception("Falha ao ler os lembretes; seguindo sem eles.")
        if self._tasks is not None:
            try:
                tasks_block = self._tasks.block_for_chat()
                if tasks_block:
                    memory_block = (memory_block + "\n\n" + tasks_block).strip()
            except Exception:  # noqa: BLE001
                log.exception("Falha ao ler as tarefas; seguindo sem elas.")
        self._chat_lessons = []
        try:   # Fase 7: o que ele aprendeu com as suas reações e o que ele criou
            found = lessons.relevant(conn, "chat", kept_rows[-1]["content"], limit=4)
            self._chat_lessons = [r["id"] for r in found]
            extra = [lessons.block(found, "chat")]
            if previous_at and (now - datetime.fromisoformat(previous_at)).total_seconds() >= AWAY_SECONDS:
                away = day_mod.since(conn, previous_at, now.astimezone(timezone.utc).isoformat(timespec="milliseconds"))
                if away:   # Fase 9: ele voltou depois de um tempo; o Nym tem o que contar do próprio dia
                    extra.append("Enquanto ele esteve fora, você fez isto (registros reais; se encaixar, conte uma ou duas "
                                 "coisas do seu jeito, sem listar tudo):\n" + away[:1500])
            active_projects = projects_mod.active(conn)
            if active_projects:
                extra.append("Seus projetos em andamento: " + "; ".join(p["title"] for p in active_projects))
            watched = vision.recent_videos_block(conn, limit=3)
            if watched:
                extra.append(watched)
            made = creation_store.recent_lines(conn, limit=5)
            if made:
                extra.append("Suas criações recentes (no seu ateliê):\n" + "\n".join(f"- {l}" for l in made))
            extra_block = "\n\n".join(b for b in extra if b)
            if extra_block:
                memory_block = (memory_block + "\n\n" + extra_block).strip()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao ler lições/criações; seguindo sem elas.")
        try:   # Fase 7.1: gostos próprios (recalculados de registros reais, no máximo a cada 20 min)
            if tastes.is_stale(conn, now):
                with transaction(conn):
                    tastes.refresh(conn, now)
            memory_block = (memory_block + "\n\n" + tastes.render_block(tastes.active(conn, limit=8))).strip()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao calcular os gostos; seguindo sem eles.")
        try:   # Fase 10: o corpinho de vpet (desgaste calculado do tempo real)
            with transaction(conn):
                pet_info = pet.status(conn, now, self._tz or now.astimezone().tzinfo)
            memory_block = (memory_block + "\n\n" + pet.render_block(pet_info, now)).strip()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao ler o vpet; seguindo sem ele.")
        try:   # Fase 10: com quem ele fala (dono ou visita) e pedidos explícitos que já viraram objetivo
            with transaction(conn):
                speaker.observe(conn, current["content"], now, previous_at)
            who = speaker.block(conn, now)
            if who:
                memory_block = (who + "\n\n" + memory_block).strip()
            asked = self._auto_commit(conn, current)
            if asked:
                memory_block = (memory_block + "\n\n" + asked).strip()
            if asks_about_today(current["content"]):
                start, end = day_mod.bounds(now.astimezone(self._tz or now.astimezone().tzinfo).date(), self._tz)
                today = day_mod.since(conn, start, end)
                memory_block = (memory_block + "\n\n" + (
                    "O que você fez HOJE sozinho (registros reais; responda só com isto, e se estiver vazio diga que ainda não "
                    "fez nada de concreto hoje):\n" + (today or "- nada registrado ainda"))).strip()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao ler quem fala/pedidos; seguindo sem isso.")
        self._pending_progress = False
        try:   # Fase 10.1: ele cobra um pedido anterior -> o estado REAL do objetivo; e o ritmo da fala (tamanho, clima)
            if requests_mod.asks_progress(current["content"]):
                floor = (now - timedelta(days=2)).astimezone(timezone.utc).isoformat(timespec="milliseconds")
                text_status, self._pending_progress = requests_mod.status_block(requests_mod.latest_chat_goal(conn, floor))
                if text_status:
                    memory_block = (memory_block + "\n\n" + text_status).strip()
            self._recent_replies = [r["content"] for r in kept_rows if r["role"] == "assistant"][-rhythm.QUESTION_STREAK:]
            tone = rhythm.block(current["content"], self._recent_replies)
            if tone:
                memory_block = (memory_block + "\n\n" + tone).strip()
        except Exception:  # noqa: BLE001
            self._recent_replies = []
            log.exception("Falha ao ler o estado do pedido/ritmo; seguindo sem isso.")
        state_block = ""
        if self._state is not None:
            try:
                state_block = self._state.block_for_chat(current["id"])
            except Exception:  # noqa: BLE001
                log.exception("Falha ao calcular o estado interno; seguindo sem ele.")
        time_text = time_block(now, previous_at, self._tz)
        dynamic = "\n\n".join(b for b in (time_text, state_block, memory_block) if b)
        system = self._identity.build_system_prompt()          # só a parte fixa: o Ollama reaproveita o processamento
        history = self._history_messages(conn, conversation_id, kept_rows)
        last = history[-1]
        history[-1] = ChatMessage(last.role, f"{CONTEXT_OPEN}\n{dynamic}\n{CONTEXT_CLOSE}\n\n{last.content}")
        context = [ChatMessage("system", system), *history]
        return context, used

    def _auto_commit(self, conn, current) -> str:
        """Pedido explícito do dono ("procure...", "pesquise...") vira objetivo na hora, pelo código (ver requests.py)."""
        if self._tasks is None or not self._settings.chat_commitments or current["role"] != "user":
            return ""
        description = requests_mod.explicit_request(current["content"])
        if not description:
            return ""
        goal = self._tasks.commit_from_chat(description, current["id"], current["content"])
        if goal is None:   # já havia um objetivo aberto parecido: o bloco de tarefas já mostra o estado real dele
            return ""
        self._auto_goals.append({"goal_id": goal["id"], "description": goal["description"]})
        return requests_mod.pending_block(description, goal["id"])

    def _window(self, conn, conversation_id: str) -> list:
        """Mensagens do histórico enviadas ao modelo, numa janela que anda EM BLOCOS.

        Uma janela que desliza uma mensagem por vez muda o começo da conversa a cada fala, e o Ollama precisa
        reprocessar tudo (no Windows do dono: ~3000 tokens, 2,5-3 s por resposta). Andando em blocos de
        `context_messages // 2`, o começo fica idêntico por várias falas seguidas e só o fim é calculado.
        A janela tem entre N e N + bloco - 1 mensagens; o corte por tamanho também é feito em blocos."""
        n = self._settings.context_messages
        block = max(1, n // 2)
        total = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conversation_id,)).fetchone()[0]
        start = max(0, total - n)
        start -= start % block
        rows = conn.execute(
            "SELECT id, role, content, model, created_at, initiative FROM messages WHERE conversation_id = ? "
            "ORDER BY id LIMIT -1 OFFSET ?", (conversation_id, start)).fetchall()
        budget = self._settings.context_chars
        while len(rows) > 1 and sum(len(r["content"]) for r in rows) > budget:
            rows = rows[min(block, len(rows) - 1):]          # corta do começo, também em blocos
        return list(rows)

    @staticmethod
    def _previous_at(conn, conversation_id: str, message_id: int) -> str | None:
        row = conn.execute("SELECT created_at FROM messages WHERE conversation_id = ? AND id < ? ORDER BY id DESC LIMIT 1",
                           (conversation_id, message_id)).fetchone()
        return row["created_at"] if row else None

    def _history_messages(self, conn, conversation_id: str, rows) -> list[ChatMessage]:
        """Histórico para o modelo. Falas do usuário que vieram depois de uma pausa longa ganham uma marca
        ("[2 dias depois]"), para o Nym perceber que o tempo passou também no meio do histórico."""
        out: list[ChatMessage] = []
        try:   # Fase 8: o que ele viu junto de cada fala (descrição registrada, não a imagem)
            seen = vision.for_messages(conn, [r["id"] for r in rows if r["role"] == "user"])
        except sqlite3.Error:
            seen = {}
        for i, r in enumerate(rows):
            content = r["content"]
            if r["role"] == "user":
                prev = rows[i - 1]["created_at"] if i else self._previous_at(conn, conversation_id, r["id"])
                mark = gap_marker(prev, r["created_at"])
                if mark:
                    content = f"{mark} {content}"
                if r["id"] in seen:
                    content = f"{content}\n{vision.history_note(seen[r['id']])}"
            out.append(ChatMessage(r["role"], content))
        return out

    def get_history(
        self, limit: int = DEFAULT_HISTORY_LIMIT, before_id: int | None = None
    ) -> dict:
        """Mensagens do chat único, em ordem cronológica. `before_id` pagina para trás."""
        limit = max(1, min(int(limit), MAX_HISTORY_LIMIT))
        conn = connect(self._settings.db_path)
        try:
            cid = self._main_conversation(conn)
            if cid is None:
                return {"messages": [], "has_more": False}
            rows = repo.messages_page(conn, cid, limit + 1, before_id)
            has_more = len(rows) > limit
            msgs = [dict(r) for r in reversed(rows[:limit])]
            try:
                reactions = feedback.for_messages(conn, [m["id"] for m in msgs if m["role"] == "assistant"])
            except Exception:  # noqa: BLE001 - banco antigo sem a tabela: segue sem reações
                reactions = {}
            try:
                seen = vision.for_messages(conn, [m["id"] for m in msgs if m["role"] == "user"])
            except Exception:  # noqa: BLE001
                seen = {}
            for m in msgs:
                m["reaction"] = reactions.get(m["id"])
                if m["id"] in seen:
                    m["perceptions"] = [{k: p[k] for k in ("id", "kind", "description")} for p in seen[m["id"]]]
            return {"messages": msgs, "has_more": has_more}
        finally:
            conn.close()
