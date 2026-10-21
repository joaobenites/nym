"""Estado interno do Nym (à la Digimon): energia, curiosidade e vínculo.

É um MODELO DE ESTADO calculado de dados reais do próprio sistema, não um sentimento. Serve para variar o jeito de falar
(ritmo, tamanho, assuntos) e, no futuro, para o avatar. A interface e o prompt deixam isso explícito.

Definições (todas em 0..1):
- energia: uma reserva que esvazia um pouco a cada mensagem SUA e se recupera sozinha com o tempo (recuperação
  exponencial, ~95% em 7 h; um sonho conta como um cochilo extra). Calculada sobre a linha do tempo real das últimas
  48 h. Conversa num ritmo normal fica em equilíbrio ("tranquilo"); só uma conversa longa e intensa cansa.
  Mensagens dele mesmo (respostas e iniciativas) não cansam.
- curiosidade: sobe com pendências (perguntas em aberto, explorações pendentes, hipóteses sem confirmar; saturando
  devagar, para um acúmulo antigo não deixar ele "curioso" para sempre) e com novidades ainda não sonhadas; cai um
  pouco logo depois de ele pesquisar/explorar (curiosidade satisfeita).
- vínculo: cresce com a regularidade (dias com conversa nos últimos 30) e o volume total de mensagens suas,
  e esmaece devagar com a ausência.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import Settings
from app.database.connection import connect

ENERGY_START = 0.85         # como ele está no início da janela de 48 h (descansado)
ENERGY_PER_MESSAGE = 0.015  # quanto cada mensagem sua gasta
RECOVERY_HOURS = 2.5        # constante de recuperação (6 h de pausa ≈ 90% recuperado)
DREAM_BONUS = 0.10
DREAM_IDLE_HOURS = 1.0    # sonho só vale como cochilo se você estava fora há 1 h ou mais
WINDOW_HOURS = 48

NOTE = "Estado calculado a partir de dados reais do sistema; não é sentimento."


@dataclass(frozen=True)
class StateInputs:
    hours_since_last_chat: float | None   # None = nunca conversaram
    messages_last_2h: int
    chat_days_last_30: int
    total_user_messages: int
    open_questions: int
    pending_explorations: int
    unconfirmed_hypotheses: int
    new_memories_since_dream: int
    energy_reserve: float | None = None   # reserva calculada da linha do tempo (None = nunca conversaram)
    user_messages_last_hour: int = 0
    dreams_last_48h: int = 0
    explored_last_3h: int = 0             # pesquisas/explorações feitas nas últimas 3 h


@dataclass(frozen=True)
class InnerState:
    energy: float
    curiosity: float
    bond: float
    label: str
    reasons: list[str]
    inputs: dict[str, Any]
    computed_at: str
    note: str = NOTE


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def compute_state(inp: StateInputs, now: datetime) -> InnerState:
    energy = _clamp(0.95 if inp.energy_reserve is None else inp.energy_reserve)   # 48 h sem conversa = descansado

    backlog = inp.open_questions + inp.pending_explorations + inp.unconfirmed_hypotheses / 5
    curiosity = _clamp(
        0.20
        + 0.35 * backlog / (backlog + 6)                                          # pendências: satura devagar
        + 0.35 * inp.new_memories_since_dream / (inp.new_memories_since_dream + 6)  # novidade ainda não "digerida"
        - min(0.30, 0.06 * inp.explored_last_3h)                                  # acabou de matar a curiosidade
    )

    regularity = min(1.0, inp.chat_days_last_30 / 30)
    volume = min(1.0, inp.total_user_messages / 500)
    base = 0.6 * regularity + 0.4 * volume
    away_days = 0.0 if inp.hours_since_last_chat is None else inp.hours_since_last_chat / 24
    bond = _clamp(base * (0.5 + 0.5 * math.exp(-away_days / 30)))

    reasons: list[str] = []
    if inp.user_messages_last_hour >= 15:
        reasons.append(f"conversa intensa ({inp.user_messages_last_hour} mensagens suas na última hora)")
    if inp.hours_since_last_chat is not None and inp.hours_since_last_chat >= 6:
        reasons.append(f"descansou {inp.hours_since_last_chat:.0f} h desde a última conversa")
    if inp.dreams_last_48h:
        reasons.append(f"{inp.dreams_last_48h} sonho(s) nas últimas 48 h")
    if inp.open_questions:
        reasons.append(f"{inp.open_questions} pergunta(s) em aberto dos sonhos")
    if inp.pending_explorations:
        reasons.append(f"{inp.pending_explorations} tarefa(s) de exploração pendente(s)")
    if inp.explored_last_3h:
        reasons.append(f"explorou {inp.explored_last_3h} coisa(s) nas últimas 3 h")
    if inp.unconfirmed_hypotheses:
        reasons.append(f"{inp.unconfirmed_hypotheses} hipótese(s) sem confirmar")
    if inp.new_memories_since_dream:
        reasons.append(f"{inp.new_memories_since_dream} lembrança(s) nova(s) desde o último sonho")

    if energy < 0.30:
        label = "cansado"
    elif curiosity >= 0.60:
        label = "curioso"
    elif inp.hours_since_last_chat is not None and inp.hours_since_last_chat >= 72:
        label = "ausente há dias"
    elif energy >= 0.75:
        label = "descansado"
    else:
        label = "tranquilo"

    return InnerState(
        energy=round(energy, 3), curiosity=round(curiosity, 3), bond=round(bond, 3), label=label,
        reasons=reasons, inputs=asdict(inp), computed_at=now.isoformat(timespec="seconds"),
    )


def render_state_block(state: InnerState) -> str:
    """Texto para o prompt. Orienta o tom sem pedir que o Nym anuncie números nem afirme sentir."""
    guidance: list[str] = []
    if state.energy < 0.35:
        guidance.append("energia baixa: respostas mais curtas e calmas")
    elif state.energy > 0.75:
        guidance.append("energia alta: pode ser mais animado")
    if state.curiosity >= 0.6:
        guidance.append("curiosidade alta: faça uma pergunta de volta quando fizer sentido")
    if state.bond < 0.2:
        guidance.append("vínculo ainda pequeno: seja mais reservado, está conhecendo o usuário")
    elif state.bond > 0.6:
        guidance.append("vínculo forte: pode ser mais à vontade e íntimo")
    summary = f"energia {state.energy:.2f}, curiosidade {state.curiosity:.2f}, vínculo {state.bond:.2f} ({state.label})"
    tips = ("; ".join(guidance) + ". ") if guidance else ""
    return (
        f"Seu estado interno agora ({summary}) é um modelo calculado de dados reais, não um sentimento. "
        f"{tips}Deixe isso colorir levemente o jeito de falar (ritmo, tamanho, assuntos), sem anunciar números. "
        "Se perguntarem se você sente de verdade, explique com honestidade que é um estado calculado."
    )


def gather_inputs(conn: sqlite3.Connection, now: datetime, before_message_id: int | None = None) -> StateInputs:
    """`before_message_id`: ignora essa mensagem e as seguintes ao medir o descanso. Durante uma resposta, a fala
    do usuário já está gravada; sem isso, "horas desde a última conversa" daria sempre quase zero."""
    main = conn.execute("SELECT value FROM app_state WHERE key = 'main_conversation_id'").fetchone()
    hours: float | None = None
    last2h = days30 = total = 0
    energy_timeline: tuple[list[float], list[float]] = ([], [])
    if main:
        cid = main["value"]
        last = conn.execute("SELECT created_at FROM messages WHERE conversation_id = ? AND initiative = 0 "
                            "AND id < ? ORDER BY id DESC LIMIT 1",
                            (cid, before_message_id if before_message_id is not None else 2**62)).fetchone()
        if last:
            hours = max(0.0, (now - _parse(last["created_at"])).total_seconds() / 3600)
        since2h = (now - timedelta(hours=2)).isoformat(timespec="milliseconds")
        last2h = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND created_at >= ? AND created_at <= ?",
                              (cid, since2h, now.isoformat(timespec="milliseconds"))).fetchone()[0]
        since48 = (now - timedelta(hours=WINDOW_HOURS)).isoformat(timespec="milliseconds")
        msgs = [max(0.0, (now - _parse(r["created_at"])).total_seconds() / 3600) for r in conn.execute(
            "SELECT created_at FROM messages WHERE conversation_id = ? AND role = 'user' AND id < ? AND created_at >= ? "
            "AND created_at <= ?", (cid, before_message_id if before_message_id is not None else 2**62, since48,
                                    now.isoformat(timespec="milliseconds")))]
        dreams = [max(0.0, (now - _parse(r["ended_at"])).total_seconds() / 3600) for r in conn.execute(
            "SELECT ended_at FROM dream_cycles WHERE status IN ('completed', 'partial') AND ended_at >= ? AND ended_at <= ?",
            (since48, now.isoformat(timespec="milliseconds")))]
        energy_timeline = (msgs, dreams)
        since30 = (now - timedelta(days=30)).isoformat(timespec="milliseconds")
        days30 = conn.execute(
            "SELECT COUNT(DISTINCT substr(created_at, 1, 10)) FROM messages WHERE conversation_id = ? "
            "AND role = 'user' AND created_at >= ?", (cid, since30)).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND role = 'user'",
                             (cid,)).fetchone()[0]
    open_q = conn.execute("SELECT COUNT(*) FROM dream_items WHERE kind = 'open_question' AND status = 'open'").fetchone()[0]
    pend = conn.execute("SELECT COUNT(*) FROM exploration_tasks WHERE status = 'pending'").fetchone()[0]
    hyp = conn.execute("SELECT COUNT(*) FROM memories WHERE status = 'active' AND epistemic = 'hypothesis'").fetchone()[0]
    last_dream = conn.execute("SELECT COALESCE(ended_at, started_at) AS ref FROM dream_cycles "
                              "WHERE status IN ('completed', 'partial') ORDER BY id DESC LIMIT 1").fetchone()
    if last_dream:
        new = conn.execute("SELECT COUNT(*) FROM memories WHERE status = 'active' AND created_at > ?",
                           (last_dream["ref"],)).fetchone()[0]
    else:
        new = conn.execute("SELECT COUNT(*) FROM memories WHERE status = 'active'").fetchone()[0]
    since3h = (now - timedelta(hours=3)).isoformat(timespec="milliseconds")
    explored = conn.execute("SELECT COUNT(*) FROM research_log WHERE status IN ('ok', 'cached') AND created_at >= ?",
                            (since3h,)).fetchone()[0]
    explored += conn.execute("SELECT COUNT(*) FROM exploration_tasks WHERE status = 'done' AND updated_at >= ?",
                             (since3h,)).fetchone()[0]
    msgs, dreams = energy_timeline
    last_hour = sum(1 for h in msgs if h <= 1.0)
    reserve = energy_from_timeline(msgs, dreams) if msgs else None
    return StateInputs(hours, last2h, days30, total, open_q, pend, hyp, new, reserve, last_hour, len(dreams), explored)


def energy_from_timeline(user_msgs_hours_ago: list[float], dreams_hours_ago: list[float]) -> float:
    """Simula a reserva de energia do início da janela até agora. Entradas: há quantas horas foi cada mensagem
    sua e cada sonho concluído (dentro da janela). Pura e determinística."""
    events = sorted([(h, "msg") for h in user_msgs_hours_ago] + [(h, "dream") for h in dreams_hours_ago], key=lambda e: -e[0])
    energy, t = ENERGY_START, float(WINDOW_HOURS)
    last_msg: float | None = None   # há quantas horas foi sua última mensagem (no tempo da simulação)
    napped = False                   # só um cochilo por pausa
    for h, kind in events + [(0.0, "now")]:
        dt = max(0.0, t - h)
        energy += (1 - energy) * (1 - math.exp(-dt / RECOVERY_HOURS))
        t = h
        if kind == "msg":
            energy -= ENERGY_PER_MESSAGE
            last_msg, napped = h, False
        elif kind == "dream" and not napped and (last_msg is None or last_msg - h >= DREAM_IDLE_HOURS):
            energy += DREAM_BONUS   # sonho no meio da conversa não é descanso; só conta numa pausa de verdade
            napped = True
        energy = _clamp(energy)
    return round(energy, 4)


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class StateService:
    def __init__(self, settings: Settings, clock=None) -> None:
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def current(self, before_message_id: int | None = None) -> InnerState:
        conn = connect(self._settings.db_path)
        try:
            now = self._clock()
            return compute_state(gather_inputs(conn, now, before_message_id), now)
        finally:
            conn.close()

    def block_for_chat(self, before_message_id: int | None = None) -> str:
        return render_state_block(self.current(before_message_id))
