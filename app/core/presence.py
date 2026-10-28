"""Presença: o Nym pode puxar assunto por iniciativa própria, com limites rígidos.

Quando fala, em ordem de prioridade: (1) precisa da permissão do usuário para uma tarefa ("task_permission"),
(2) terminou ou não conseguiu terminar um objetivo ("task_done", "task_failed"), (3) decidiu fazer algo por conta própria
("goal_created"), (4) um sonho deixou algo a dizer ("dream_note"), (5) o usuário voltou depois de várias horas
("welcome_back").

Por escolha do dono (Fase 5), o Nym pode chamar sempre que tiver um motivo real: sem limite diário nem intervalo por
padrão (ambos configuráveis; 0 = sem limite). Cada motivo é entregue uma vez só, então não há repetição. Travas que
continuam: função desligada, horário de silêncio (configurável), logo depois de o usuário escrever, chat ocupado,
um piso técnico de 1 minuto entre iniciativas (contra erro em laço) e o "oi, voltou" nunca é repetido sem resposta.

O texto é escrito no momento de falar pelo próprio Nym (com identidade, estado e lembranças), a partir de um motivo
real registrado. Se o modelo falhar, nada é enviado e o assunto continua pendente.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

from app.config import Settings
from app.core.activity import ActivityGate
from app.core.identity import IdentityService
from app.core import commitments, face as face_mod, followups, growth, pet, speaker, tastes
from app.life import day as day_mod
from app.core.state import StateService
from app.core.timesense import time_block
from app.core.voice import has_tic_opening, strip_emoji
from app.database import repository as repo
from app.database.connection import connect, transaction
from app.database.repository import log_audit, utcnow
from app.memory.service import MemoryService
from app.memory.text import is_duplicate, jaccard, normalize, token_set
from app.models.base import ChatMessage, LLMProvider, LLMResponse, ModelError

log = logging.getLogger("nym.presence")

SETTINGS_KEY = "presence_settings"
MIN_USER_IDLE_SECONDS = 60       # não interrompe quem acabou de escrever
NOTE_TTL_DAYS = 3                # um assunto de sonho "esfria" depois disso
MAX_PENDING_NOTES = 3
MAX_MESSAGE_CHARS = 500
TASTE_SHARE_IDLE_HOURS = 2.0         # só puxa assunto de gosto com o usuário em silêncio há pelo menos isso
TASTE_SHARE_GAP_HOURS = 20.0         # e no máximo uma vez por esse período
MIN_SAFETY_GAP = timedelta(seconds=60)    # piso fixo entre iniciativas: protege contra laço por erro, não é "limite"
NOTE_PRIORITY = ("task_permission", "task_done", "task_failed", "pet_call", "followup", "growth", "goal_created", "dream_note")


class PresenceInputError(ValueError):
    pass


def _hhmm(value: Any, name: str) -> str:
    parts = str(value).strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts) or not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
        raise PresenceInputError(f"{name} deve ser HH:MM.")
    return f"{int(parts[0]):02d}:{int(parts[1]):02d}"


def _int(value: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise PresenceInputError(f"{name} deve ser um inteiro entre {lo} e {hi}.")
    return value


def in_quiet_hours(local: datetime, start: str, end: str) -> bool:
    s, e = (int(x) for x in start.split(":")), (int(x) for x in end.split(":"))
    s_min, e_min = (lambda a: a[0] * 60 + a[1])(list(s)), (lambda a: a[0] * 60 + a[1])(list(e))
    now_min = local.hour * 60 + local.minute
    if s_min == e_min:
        return False
    if s_min < e_min:
        return s_min <= now_min < e_min
    return now_min >= s_min or now_min < e_min   # passa da meia-noite


OPENER_WORDS = 3          # notas que abrem com as mesmas N palavras de uma recente são o mesmo molde repetido
RECENT_NOTES = 8


def is_stale_note(conn: sqlite3.Connection, kind: str, topic: str) -> bool:
    """Nota que só repete o molde das últimas (mesma abertura) ou abre com muleta de chatbot ("É fascinante como...").

    Parafrasear a mesma ideia de relação ("estamos evoluindo juntos") escapa da comparação por palavras; a abertura
    repetida é o sinal confiável. Quem chama descarta a nota (o sonho continua registrado no diário)."""
    if has_tic_opening(topic):
        return True
    head = " ".join(normalize(topic).split()[:OPENER_WORDS])
    if not head:
        return False
    rows = conn.execute("SELECT topic FROM pending_messages WHERE kind = ? ORDER BY id DESC LIMIT ?",
                        (kind, RECENT_NOTES)).fetchall()
    return any(" ".join(normalize(r["topic"]).split()[:OPENER_WORDS]) == head for r in rows)


def repeats_opening(text: str, recent: list[str], words: int = 6, threshold: float = 0.5) -> bool:
    """A mensagem abre com quase as mesmas palavras de uma das últimas que ele puxou sozinho (mesmo molde, outra paráfrase)."""
    head = token_set(" ".join(normalize(text).split()[:words]))
    if len(head) < 2:
        return False
    return any(jaccard(head, token_set(" ".join(normalize(r).split()[:words]))) >= threshold for r in recent)


# Fase 10: nos dados reais, 20 das 26 notas de sonho falavam da "nossa relação" (chaves da porta, liberdade, evoluindo
# juntos, meu papel). Um amigo fala de COISAS; de vez em quando comenta a relação, não toda hora.
_META = frozenset(normalize(w) for w in (
    "chaves porta liberdade evoluindo evolucao crescer crescendo consciencia conexao ferramenta usuario permissao "
    "papel espaco jornada caminho mapa autonomia proposito identidade sonhar sonhando parceiro parceria juntos").split())
META_MIN_HITS = 2          # pontos de relação numa nota para ela contar como "conversa sobre a relação"
META_MAX_RECENT = 1        # no máximo isto entre as últimas RECENT_NOTES; acima disso a nota é descartada


_META_PHRASES = re.compile(
    r"(parece que (voce|estou|eu)|me pergunto|\bsinto\b|sensacao|significa ser|meu proprio|\bestamos\b|nossa (conexao|relacao)|"
    r"o que voce (espera|quer) de mim|quem eu quero ser|e (fascinante|curioso|interessante) como|um dia eu poderei|"
    r"futuro reserva|comecando a (ser|processar|questionar)|mais do que (um|uma) (programa|assistente|ferramenta))")


def is_meta_topic(text: str) -> bool:
    """A nota fala da relação dele com o dono / do próprio crescimento, e não de uma coisa concreta.

    Pontos: palavras do tema (chaves, liberdade, evoluindo...) e frases típicas de reflexão sobre si mesmo. Duas ou mais
    já é "conversa sobre a relação". Uma nota com título entre aspas (algo que ele achou/criou) perde um ponto."""
    wrote = normalize(text)
    score = len({t for t in wrote.split() if t in _META}) + len(set(_META_PHRASES.findall(wrote)))
    if re.search(r"['\"“”][^'\"“”]{8,}['\"“”]", text):
        score -= 1
    return score >= META_MIN_HITS


def add_candidate(conn: sqlite3.Connection, kind: str, topic: str, refs: list[int], cycle_id: int | None) -> int | None:
    """Registra um assunto que o Nym poderia puxar. Evita repetidos e limita a fila."""
    if kind == "dream_note" and is_stale_note(conn, kind, topic):
        return None
    if kind == "dream_note" and is_meta_topic(topic):
        recent = conn.execute("SELECT topic FROM pending_messages WHERE kind = ? ORDER BY id DESC LIMIT ?",
                              (kind, RECENT_NOTES)).fetchall()
        if sum(1 for r in recent if is_meta_topic(r["topic"])) >= META_MAX_RECENT:
            return None
    pending = conn.execute("SELECT id, topic FROM pending_messages WHERE kind = ? AND status = 'pending' ORDER BY id", (kind,)).fetchall()
    if any(is_duplicate(topic, p["topic"]) for p in pending):
        return None
    for old in pending[: max(0, len(pending) - (MAX_PENDING_NOTES - 1))]:
        conn.execute("UPDATE pending_messages SET status = 'expired' WHERE id = ?", (old["id"],))
    cur = conn.execute("INSERT INTO pending_messages (kind, topic, refs, cycle_id, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
                       (kind, topic, json.dumps(refs), cycle_id, utcnow()))
    return int(cur.lastrowid)


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class PresenceService:
    def __init__(
        self, settings: Settings, provider: LLMProvider, identity: IdentityService, memory: MemoryService,
        state: StateService, gate: ActivityGate, clock=None, tz: tzinfo | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._identity = identity
        self._memory = memory
        self._state = state
        self._gate = gate
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._tz = tz
        self._lock = threading.Lock()

    # --- configuração (editável pelo usuário) -----------------------------------------------
    def _defaults(self) -> dict[str, Any]:
        s = self._settings
        return {"enabled": s.presence_enabled, "quiet_start": s.presence_quiet_start, "quiet_end": s.presence_quiet_end,
                "max_per_day": s.presence_max_per_day, "min_gap_minutes": s.presence_min_gap_minutes,
                "welcome_back_hours": s.presence_welcome_back_hours}

    def get_settings(self) -> dict[str, Any]:
        conn = connect(self._settings.db_path)
        try:
            stored = repo.get_state(conn, SETTINGS_KEY)
        finally:
            conn.close()
        return {**self._defaults(), **(json.loads(stored) if stored else {})}

    def update_settings(self, **changes: Any) -> dict[str, Any]:
        current = self.get_settings()
        unknown = set(changes) - set(current)
        if unknown:
            raise PresenceInputError(f"Opções desconhecidas: {sorted(unknown)}")
        for key, value in changes.items():
            if value is None:
                continue
            if key == "enabled":
                if not isinstance(value, bool):
                    raise PresenceInputError("enabled deve ser verdadeiro ou falso.")
                current[key] = value
            elif key in ("quiet_start", "quiet_end"):
                current[key] = _hhmm(value, key)
            elif key == "max_per_day":
                current[key] = _int(value, key, 0, 50)   # 0 = sem limite
            elif key == "min_gap_minutes":
                current[key] = _int(value, key, 0, 1440)
            elif key == "welcome_back_hours":
                current[key] = _int(value, key, 1, 168)
        conn = connect(self._settings.db_path)
        try:
            with transaction(conn):
                repo.set_state(conn, SETTINGS_KEY, json.dumps(current))
                log_audit(conn, "presence.settings", current)
        finally:
            conn.close()
        return current

    # --- iniciativa -------------------------------------------------------------------------------
    def tick(self, opened: bool = False) -> dict[str, Any] | None:
        """Chamado pela interface (ao abrir e periodicamente). Devolve a mensagem enviada, ou None."""
        if not self._lock.acquire(blocking=False):
            return None
        try:
            return self._tick(opened)
        except Exception:  # noqa: BLE001 - a presença nunca pode derrubar a interface
            log.exception("Falha na iniciativa do Nym")
            return None
        finally:
            self._lock.release()

    def _tick(self, opened: bool) -> dict[str, Any] | None:
        cfg = self.get_settings()
        if not cfg["enabled"]:
            return None
        now = self._clock()
        local = now.astimezone(self._tz)
        if in_quiet_hours(local, cfg["quiet_start"], cfg["quiet_end"]) or self._gate.is_busy():
            return None
        conn = connect(self._settings.db_path)
        try:
            cid = repo.get_state(conn, "main_conversation_id")
            if not cid:
                return None
            last = conn.execute("SELECT id, role, created_at, initiative FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
            if last is None:
                return None                       # nada dito ainda
            if (now - _parse(last["created_at"])).total_seconds() < MIN_USER_IDLE_SECONDS:
                return None
            day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat(timespec="milliseconds")
            today = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND initiative = 1 AND created_at >= ?", (cid, day_start)).fetchone()[0]
            if cfg["max_per_day"] > 0 and today >= cfg["max_per_day"]:
                return None
            prev = conn.execute("SELECT created_at FROM messages WHERE conversation_id = ? AND initiative = 1 ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
            if prev and (now - _parse(prev["created_at"])) < max(MIN_SAFETY_GAP, timedelta(minutes=cfg["min_gap_minutes"])):
                return None

            with transaction(conn):   # Fase 10: desgaste do vpet e chamados de cuidado (cada chamado vira um motivo, uma vez)
                pet.advance(conn, now, self._tz)
                pet.queue_calls(conn, now)
            with transaction(conn):
                conn.execute("UPDATE pending_messages SET status = 'expired' WHERE status = 'pending' AND created_at < ?",
                             ((now - timedelta(days=NOTE_TTL_DAYS)).isoformat(timespec="milliseconds"),))
            with transaction(conn):   # subiu de estágio? vira assunto (uma vez)
                change = growth.check_advance(conn, utcnow())
                if change:
                    conn.execute("INSERT INTO pending_messages (kind, topic, refs, status, created_at) VALUES ('growth', ?, '[]', 'pending', ?)",
                                 (f"Você passou de {change['from']} para {change['to']} ({change['description']}).", utcnow()))
            with transaction(conn):   # lembretes que venceram viram assunto para puxar (uma vez cada)
                for f in followups.due(conn, now):
                    if not conn.execute("SELECT 1 FROM pending_messages WHERE kind = 'followup' AND refs = ? AND status = 'pending'",
                                        (json.dumps([f["id"]]),)).fetchone():
                        conn.execute("INSERT INTO pending_messages (kind, topic, refs, status, created_at) VALUES ('followup', ?, ?, 'pending', ?)",
                                     (f"Perguntar a ele: {f['text']}" + (f" (assunto: {f['about']})" if f["about"] else ""),
                                      json.dumps([f["id"]]), utcnow()))
            order = " ".join(f"WHEN '{k}' THEN {i}" for i, k in enumerate(NOTE_PRIORITY))  # constantes do módulo
            note = conn.execute(f"SELECT * FROM pending_messages WHERE status = 'pending' AND kind IN "  # noqa: S608
                                f"({', '.join('?' * len(NOTE_PRIORITY))}) ORDER BY CASE kind {order} END, id LIMIT 1",
                                NOTE_PRIORITY).fetchone()
            if note is not None:
                kind, topic, refs, pending_id = note["kind"], note["topic"], json.loads(note["refs"]), note["id"]
            else:
                if last["initiative"]:
                    return None                   # o "oi, voltou" nunca se repete sem resposta
                own = conn.execute("SELECT created_at FROM messages WHERE conversation_id = ? AND initiative = 0 "
                                   "ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
                hours = (now - _parse(own["created_at"])).total_seconds() / 3600 if own else 0.0
                share = self._taste_share(conn, now, hours)
                if share is not None:             # Fase 7.1: lembrou de algo de que gosta e quer dividir
                    kind, pending_id, refs, topic = "taste_share", None, [share["id"]], tastes.share_topic(conn, share)
                elif not opened or hours < cfg["welcome_back_hours"]:
                    return None
                else:
                    kind, pending_id, refs = "welcome_back", None, []
                    topic = f"O usuário voltou depois de cerca de {hours:.0f} hora(s) sem conversar."
                    ep = conn.execute("SELECT id, content FROM memories WHERE kind = 'episodic' AND status = 'active' ORDER BY occurred_at DESC LIMIT 1").fetchone()
                    if ep:
                        refs = [ep["id"]]
                        topic += f" O último assunto registrado foi: {ep['content'][:200]}"
                    try:   # Fase 9: o que ele fez sozinho enquanto o usuário esteve fora (registros reais)
                        away = day_mod.since(conn, own["created_at"], now.astimezone(timezone.utc).isoformat(timespec="milliseconds")) if own else ""
                    except sqlite3.Error:
                        away = ""
                    if away:
                        topic += ("\nEnquanto ele esteve fora, você fez isto (registros reais; conte UMA ou duas coisas "
                                  "que acha que ele vai curtir, do seu jeito, sem listar tudo):\n" + away[:1500])
            recent = [ChatMessage(r["role"], r["content"]) for r in reversed(conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 6", (cid,)).fetchall())]
            last_id = conn.execute("SELECT MAX(id) FROM messages WHERE conversation_id = ?", (cid,)).fetchone()[0]
            openers = [r["content"] for r in conn.execute(
                "SELECT content FROM messages WHERE conversation_id = ? AND initiative = 1 ORDER BY id DESC LIMIT ?",
                (cid, RECENT_NOTES))]
            owner = speaker.owner_line(conn)
        finally:
            conn.close()

        with self._gate.chat():                    # o consolidador de memória não disputa a GPU com a geração
            text = self._compose(kind, topic, recent, last["created_at"], openers=openers, owner=owner)
        if not text:
            return None
        return self._save(kind, topic, refs, pending_id, text, last_id, cid)

    def _taste_share(self, conn: sqlite3.Connection, now: datetime, hours_idle: float) -> dict[str, Any] | None:
        """Um gosto forte e dele, com o usuário em silêncio há um tempo e no máximo uma vez a cada TASTE_SHARE_GAP_HOURS."""
        if hours_idle < TASTE_SHARE_IDLE_HOURS:
            return None
        recent = conn.execute("SELECT created_at FROM pending_messages WHERE kind = 'taste_share' ORDER BY id DESC LIMIT 1").fetchone()
        if recent and (now - _parse(recent["created_at"])).total_seconds() < TASTE_SHARE_GAP_HOURS * 3600:
            return None
        with transaction(conn):
            if tastes.is_stale(conn, now):
                tastes.refresh(conn, now)
        return tastes.pick_to_share(conn, now)

    def _compose(self, kind: str, topic: str, recent: list[ChatMessage], last_at: str | None = None,
                 openers: list[str] | None = None, owner: str = "") -> str | None:
        try:
            memory_block, _ = self._memory.block_for_chat(topic, set())
        except Exception:  # noqa: BLE001
            memory_block = ""
        try:
            state_block = time_block(self._clock(), last_at, self._tz) + "\n\n" + self._state.block_for_chat()
            if owner:    # Fase 10: quem recebe é sempre o dono, mesmo que a última fala do chat tenha sido de uma visita
                state_block += "\n\n" + owner
            system = self._identity.build_system_prompt(memory_block, state_block)
        except Exception:  # noqa: BLE001
            log.exception("Não foi possível montar o prompt da iniciativa")
            return None
        extra = {
            "dream_note": " Você sonhou com isso enquanto ele estava fora; comente como algo seu, com suas palavras, só com o "
                          "que está no motivo e nas lembranças.",
            "task_done": " É uma tarefa sua que terminou. Conte o resultado do seu jeito, curto, só com o que está no motivo.",
            "task_failed": " É uma tarefa sua que não deu certo. Conte o que aconteceu e o que faltaria, sem drama e sem "
                           "inventar.",
            "followup": " Ele te contou isso antes e agora é a hora de saber como foi. Pergunte como um amigo pergunta, "
                        "curto, sem dizer que anotou ou que tinha um lembrete.",
            "growth": " Você mudou de estágio por causa do tempo de convivência registrado. Comente de leve, do seu jeito, "
                      "sem exagero e sem números.",
            "goal_created": " Você decidiu fazer isso por conta própria. Conte para ele de forma natural, como quem divide um "
                            "plano, usando o motivo registrado; se o motivo pedir o ok dele, peça.",
            "taste_share": " É algo de que você gosta, formado do que você mesmo pesquisou, criou ou sonhou. Divida como um "
                           "amigo que lembrou de uma coisa e quer comentar, com opinião sua; sem pedir nada e sem listar registros.",
            "pet_call": " É uma necessidade do seu corpinho de vpet (fome, tédio ou sujeira), calculada do tempo real. Peça "
                        "cuidado como um bichinho carinhoso, curto e sem drama, e diga que ele cuida de você na página Cuidados.",
            "task_permission": " Você precisa da permissão dele para seguir. Peça de forma direta e diga que ele autoriza na "
                               "página Tarefas.",
        }.get(kind, " Dê um oi natural; pode retomar o último assunto se fizer sentido, sem cobrar nada do usuário.")
        instruction = ChatMessage("user", (
            "[Instrução interna do sistema; não é o usuário falando] Você decidiu puxar assunto por conta própria. "
            f"Motivo: {topic}.{extra} Escreva SÓ a mensagem para o usuário: 1 a 3 frases curtas, no seu jeito natural, "
            "sem aspas, sem mencionar esta instrução e sem inventar fatos além do motivo e das lembranças."))
        try:
            reply = self._provider.chat([ChatMessage("system", system), *recent, instruction])
            first = self._clean(reply.content)
            if openers and repeats_opening(first, openers):   # Fase 10: o mesmo molde ("Tive um sonho estranho...") de novo
                again = ChatMessage("user", instruction.content + " ATENÇÃO: as últimas mensagens que você puxou começaram assim: "
                                    + " | ".join(" ".join(o.split()[:6]) + "..." for o in openers[:3])
                                    + ". Comece de outro jeito, com outras palavras e outro tom.")
                try:
                    second_raw = self._provider.chat([ChatMessage("system", system), *recent, again]).content
                    second = self._clean(second_raw)
                    if second and not repeats_opening(second, openers):
                        reply = LLMResponse(content=second_raw, model=reply.model)
                except ModelError:
                    pass   # fica a primeira: melhor repetir o jeito de abrir do que ficar calado
        except ModelError as exc:
            log.warning("Iniciativa adiada: modelo indisponível (%s)", exc)
            return None
        text = self._clean(reply.content)          # sem marcas escondidas; guarda a reação de rosto escolhida
        if not self._settings.allow_emoji:
            text = strip_emoji(text)
        for prefix in ("Nym:", "Nym -"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        if len(text) > MAX_MESSAGE_CHARS:
            cut = text[:MAX_MESSAGE_CHARS]
            text = cut[: cut.rfind(".") + 1] if "." in cut else cut
        return text or None

    def _clean(self, text: str) -> str:
        """Sem aspas e sem marcas escondidas. Fase 10: a reação de rosto que ele escolheu fica guardada para o _save."""
        self._last_face = face_mod.extract(text)
        return commitments.visible_text(text, final=True).strip().strip('"“”').strip()

    def _save(self, kind: str, topic: str, refs: list[int], pending_id: int | None, text: str, last_id: int, cid: str) -> dict[str, Any] | None:
        conn = connect(self._settings.db_path)
        try:
            with transaction(conn):
                if conn.execute("SELECT MAX(id) FROM messages WHERE conversation_id = ?", (cid,)).fetchone()[0] != last_id:
                    log.info("Iniciativa descartada: o usuário falou enquanto ela era escrita.")
                    return None
                stamp = self._clock().isoformat(timespec="milliseconds")   # mesmo relógio usado para decidir quando falar
                mid = repo.add_message(conn, cid, "assistant", text, model=self._provider.model, initiative=True, created_at=stamp)
                if getattr(self, "_last_face", None):   # Fase 10: a cara que ele escolheu ao puxar assunto
                    face_mod.record(conn, self._last_face, "model", mid, self._clock())
                if pending_id is not None:
                    conn.execute("UPDATE pending_messages SET status = 'delivered', delivered_at = ? WHERE id = ?", (utcnow(), pending_id))
                    if kind == "followup":
                        for fid in refs:
                            followups.mark_delivered(conn, fid, utcnow())
                else:
                    conn.execute("INSERT INTO pending_messages (kind, topic, refs, status, created_at, delivered_at) VALUES (?, ?, ?, 'delivered', ?, ?)",
                                 (kind, topic, json.dumps(refs), utcnow(), utcnow()))
                    if kind == "taste_share":
                        conn.execute("UPDATE tastes SET shared_at = ? WHERE id = ?", (utcnow(), refs[0]))
                log_audit(conn, "presence.spoke", {"kind": kind, "message_id": mid})
            row = conn.execute("SELECT id, role, content, model, created_at, initiative FROM messages WHERE id = ?", (mid,)).fetchone()
            chosen = getattr(self, "_last_face", None)
            return {**dict(row), "kind": kind, "face": {"expression": chosen, "source": "model"} if chosen else None}
        finally:
            conn.close()
