"""Cuidados do Nym no estilo vpet/tamagotchi/Digimon (Fase 10).

É um MODELO CALCULADO, como um bichinho de jogo: as barras (fome, diversão, higiene, disciplina, carinho) descem com o
tempo real e sobem quando o usuário cuida. Não é sofrimento nem prova de sentimento; a interface e o prompt dizem isso.
O código decide tudo (números, recusas, chamados, descuidos); o modelo só fala sobre o que está registrado aqui.

Regras de projeto:
- Nada aqui mata ou apaga o Nym: as barras têm piso. Descuido só deixa ele manhoso e fica registrado.
- Passou muito tempo com o programa fechado? O desgaste é limitado a CATCH_UP_CAP_HOURS (o Nym não "sofre" a noite toda).
- À noite (fuso do computador) o desgaste cai bastante; dormindo, mais ainda. "Modo jogo" (pausa) congela tudo.
- Chamados: quando uma necessidade passa do limite ele "chama" (uma vez). Sem resposta em CALL_GRACE_MIN = descuido.
"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Callable

STATS = ("fullness", "fun", "hygiene", "discipline", "affection")
FLOOR = 5.0
CATCH_UP_CAP_HOURS = 6.0
NIGHT_START, NIGHT_END = 23, 7
NIGHT_FACTOR = 0.4
SLEEP_FACTOR = 0.25
DECAY_PER_HOUR = {"fullness": 4.0, "fun": 3.0, "hygiene": 1.0, "affection": 0.4, "discipline": 0.15}
MESS_DELAY_HOURS = 2.5
MESS_HYGIENE_PER_HOUR = 1.5          # cada sujeirinha parada piora a higiene além do desgaste normal
CALL_AT = {"fome": 30.0, "tedio": 25.0, "sujeira": 30.0}
CALL_RESOLVE_MARGIN = 15.0
CALL_GRACE_MIN = 60.0
CALL_REOPEN_HOURS = 3.0
TRAIN_COOLDOWN_MIN = 30.0
PLAY_COOLDOWN_MIN = 5.0
MAX_SLEEP_HOURS = 10
DEFAULT_SLEEP_HOURS = 6
RPS = {"pedra": "tesoura", "tesoura": "papel", "papel": "pedra"}   # chave vence o valor
ACTIONS = ("feed", "snack", "play", "clean", "train", "pet", "sleep", "wake", "pause", "resume")
NOTE = "Modelo de bichinho de jogo calculado do tempo real; não é sofrimento nem sentimento."
START = {"fullness": 70.0, "fun": 70.0, "hygiene": 85.0, "discipline": 30.0, "affection": 40.0}


class PetError(ValueError):
    """Entrada inválida (ação desconhecida, escolha inválida...). Recusas do bichinho NÃO são erro: viram resultado."""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _event(conn: sqlite3.Connection, kind: str, detail: dict[str, Any], now: datetime, tz: tzinfo) -> None:
    conn.execute("INSERT INTO pet_events (kind, detail, day, created_at) VALUES (?, ?, ?, ?)",
                 (kind, json.dumps(detail, ensure_ascii=False), now.astimezone(tz).date().isoformat(), _iso(now)))


def _row(conn: sqlite3.Connection, now: datetime) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM pet_state WHERE id = 1").fetchone()
    if row is None:
        s = START
        conn.execute("INSERT INTO pet_state (id, fullness, fun, hygiene, discipline, affection, messes, updated_at, created_at) "
                     "VALUES (1, ?, ?, ?, ?, ?, 0, ?, ?)",
                     (s["fullness"], s["fun"], s["hygiene"], s["discipline"], s["affection"], _iso(now), _iso(now)))
        row = conn.execute("SELECT * FROM pet_state WHERE id = 1").fetchone()
    return row


def _night(dt_local: datetime) -> bool:
    return dt_local.hour >= NIGHT_START or dt_local.hour < NIGHT_END


def _decay_factor(mid: datetime, tz: tzinfo, sleeping: bool) -> float:
    if sleeping:
        return SLEEP_FACTOR
    return NIGHT_FACTOR if _night(mid.astimezone(tz)) else 1.0


def advance(conn: sqlite3.Connection, now: datetime, tz: tzinfo = timezone.utc) -> dict[str, Any]:
    """Aplica o desgaste desde a última vez, abre/fecha chamados e conta descuidos. Chamar dentro de `transaction`."""
    row = _row(conn, now)
    st = {k: float(row[k]) for k in STATS}
    messes, mess_due = int(row["messes"]), _parse(row["mess_due_at"])
    sleeping, sleep_until = bool(row["sleeping"]), _parse(row["sleep_until"])
    last = _parse(row["updated_at"]) or now
    if now < last:                       # relógio andou para trás: não desgasta
        last = now
    if row["paused"]:
        conn.execute("UPDATE pet_state SET updated_at = ? WHERE id = 1", (_iso(now),))
        return {"paused": True}
    t = max(last, now - timedelta(hours=CATCH_UP_CAP_HOURS))
    while t < now:                       # passos de até 30 min: respeitam noite, sono e o horário das sujeirinhas
        if sleeping and sleep_until and sleep_until <= t:
            sleeping, sleep_until = False, None
            _event(conn, "wake", {"auto": True}, t, tz)
        end = min(now, t + timedelta(minutes=30))
        if sleeping and sleep_until and t < sleep_until < end:
            end = sleep_until
        hours = (end - t).total_seconds() / 3600
        f = _decay_factor(t + (end - t) / 2, tz, sleeping)
        for k, rate in DECAY_PER_HOUR.items():
            st[k] -= rate * hours * f
        st["hygiene"] -= MESS_HYGIENE_PER_HOUR * messes * hours
        if mess_due is not None and mess_due <= end:
            messes += 1
            _event(conn, "mess", {}, mess_due, tz)
            mess_due = None
        for k in STATS:
            st[k] = max(FLOOR, st[k])
        t = end
    if sleeping and sleep_until and sleep_until <= now:
        sleeping, sleep_until = False, None
        _event(conn, "wake", {"auto": True}, now, tz)
    conn.execute("UPDATE pet_state SET fullness=?, fun=?, hygiene=?, discipline=?, affection=?, messes=?, mess_due_at=?, "
                 "sleeping=?, sleep_until=?, updated_at=? WHERE id = 1",
                 (st["fullness"], st["fun"], st["hygiene"], st["discipline"], st["affection"], messes,
                  _iso(mess_due) if mess_due else None, int(sleeping), _iso(sleep_until) if sleep_until else None, _iso(now)))
    _update_calls(conn, now, tz, st, messes, sleeping)
    return {"paused": False}


def _needs(st: dict[str, float], messes: int) -> dict[str, bool]:
    return {"fome": st["fullness"] < CALL_AT["fome"], "tedio": st["fun"] < CALL_AT["tedio"],
            "sujeira": st["hygiene"] < CALL_AT["sujeira"] or messes >= 2}


def _resolved(need: str, st: dict[str, float], messes: int) -> bool:
    if need == "fome":
        return st["fullness"] >= CALL_AT["fome"] + CALL_RESOLVE_MARGIN
    if need == "tedio":
        return st["fun"] >= CALL_AT["tedio"] + CALL_RESOLVE_MARGIN
    return st["hygiene"] >= CALL_AT["sujeira"] + CALL_RESOLVE_MARGIN and messes < 2


def _update_calls(conn: sqlite3.Connection, now: datetime, tz: tzinfo, st: dict[str, float], messes: int, sleeping: bool) -> None:
    for call in conn.execute("SELECT * FROM pet_calls WHERE status = 'open'").fetchall():
        started = _parse(call["started_at"]) or now
        if _resolved(call["need"], st, messes):
            conn.execute("UPDATE pet_calls SET status = 'answered', closed_at = ? WHERE id = ?", (_iso(now), call["id"]))
            conn.execute("UPDATE pending_messages SET status = 'dismissed' WHERE kind = 'pet_call' AND status = 'pending' "
                         "AND refs = ?", (json.dumps([call["id"]]),))
        elif not sleeping and (now - started).total_seconds() >= CALL_GRACE_MIN * 60:   # dormindo não conta como descuido
            conn.execute("UPDATE pet_calls SET status = 'missed', closed_at = ? WHERE id = ?", (_iso(now), call["id"]))
            conn.execute("UPDATE pending_messages SET status = 'expired' WHERE kind = 'pet_call' AND status = 'pending' "
                         "AND refs = ?", (json.dumps([call["id"]]),))
            _event(conn, "care_mistake", {"need": call["need"], "call_id": call["id"]}, now, tz)
    if sleeping:
        return                           # dormindo ele não chama
    for need, active in _needs(st, messes).items():
        if not active:
            continue
        if conn.execute("SELECT 1 FROM pet_calls WHERE need = ? AND status = 'open'", (need,)).fetchone():
            continue
        recent = conn.execute("SELECT closed_at FROM pet_calls WHERE need = ? AND status = 'missed' ORDER BY id DESC LIMIT 1",
                              (need,)).fetchone()
        if recent and (now - (_parse(recent["closed_at"]) or now)).total_seconds() < CALL_REOPEN_HOURS * 3600:
            continue
        conn.execute("INSERT INTO pet_calls (need, status, started_at) VALUES (?, 'open', ?)", (need, _iso(now)))


# --- ações do usuário ------------------------------------------------------------------------------------------------
def _mins_since(value: str | None, now: datetime) -> float | None:
    dt = _parse(value)
    return None if dt is None else (now - dt).total_seconds() / 60


def act(conn: sqlite3.Connection, action: str, now: datetime, tz: tzinfo = timezone.utc, *, choice: str | None = None,
        hours: int | None = None, rng: random.Random | None = None) -> dict[str, Any]:
    """Executa uma ação. Devolve {ok, action, message, effects}. Recusas (ok=False) são respostas normais do bichinho.
    Chamar dentro de `transaction`. ValueError (PetError) só para entrada inválida."""
    if action not in ACTIONS:
        raise PetError(f"Ação desconhecida: {action}.")
    rng = rng or random.Random()
    if action == "play" and choice not in RPS:     # entrada inválida vem antes de qualquer recusa do bichinho
        raise PetError("choice deve ser pedra, papel ou tesoura.")
    advance(conn, now, tz)
    row = conn.execute("SELECT * FROM pet_state WHERE id = 1").fetchone()
    st = {k: float(row[k]) for k in STATS}
    messes, sleeping, paused = int(row["messes"]), bool(row["sleeping"]), bool(row["paused"])
    fields: dict[str, Any] = {}
    effects: dict[str, float] = {}

    def refuse(msg: str, why: str) -> dict[str, Any]:
        _event(conn, "refused", {"action": action, "why": why}, now, tz)
        return {"ok": False, "action": action, "message": msg, "effects": {}, "reason": why}

    def bump(**kw: float) -> None:
        for k, d in kw.items():
            before = st[k]
            st[k] = min(100.0, max(FLOOR, st[k] + d))
            effects[k] = round(st[k] - before, 1)

    if action == "resume":
        if not paused:
            return refuse("Já estou acordado e valendo, sem pausa.", "not_paused")
        conn.execute("UPDATE pet_state SET paused = 0, updated_at = ? WHERE id = 1", (_iso(now),))
        _event(conn, "resume", {}, now, tz)
        return {"ok": True, "action": action, "message": "Voltei! Pode contar comigo de novo.", "effects": {}}
    if action == "pause":
        if paused:
            return refuse("Já estou em pausa.", "already_paused")
        conn.execute("UPDATE pet_state SET paused = 1, updated_at = ? WHERE id = 1", (_iso(now),))
        _event(conn, "pause", {}, now, tz)
        return {"ok": True, "action": action, "message": "Tá bom, fico quietinho até você voltar (modo jogo).", "effects": {}}
    if paused:
        return refuse("Estou em pausa (modo jogo). Me tire da pausa primeiro.", "paused")

    if action == "wake":
        if not sleeping:
            return refuse("Eu não estava dormindo.", "not_sleeping")
        conn.execute("UPDATE pet_state SET sleeping = 0, sleep_until = NULL WHERE id = 1")
        _event(conn, "wake", {"auto": False}, now, tz)
        return {"ok": True, "action": action, "message": "Hmm... acordei.", "effects": {}}
    if action == "sleep":
        if sleeping:
            return refuse("Já estou dormindo.", "already_sleeping")
        h = DEFAULT_SLEEP_HOURS if hours is None else hours
        if not isinstance(h, int) or isinstance(h, bool) or not 1 <= h <= MAX_SLEEP_HOURS:
            raise PetError(f"hours deve ser um inteiro de 1 a {MAX_SLEEP_HOURS}.")
        conn.execute("UPDATE pet_state SET sleeping = 1, sleep_until = ? WHERE id = 1", (_iso(now + timedelta(hours=h)),))
        _event(conn, "sleep", {"hours": h}, now, tz)
        return {"ok": True, "action": action, "message": f"Zzz... vou dormir um pouco ({h} h).", "effects": {}}
    if sleeping:
        return refuse("Shh... estou dormindo. Me acorde se for importante.", "sleeping")

    msg = ""
    if action == "feed":
        if st["fullness"] >= 90:
            return refuse("Não quero comer agora, estou cheio.", "full")
        bump(fullness=35.0, fun=3.0)
        if row["mess_due_at"] is None:
            fields["mess_due_at"] = _iso(now + timedelta(hours=MESS_DELAY_HOURS))
        fields["last_fed_at"] = _iso(now)
        msg = "Nham nham! Obrigado pela comida."
    elif action == "snack":
        if st["fullness"] >= 95:
            return refuse("Nem cabe mais um petisco!", "full")
        bump(fullness=8.0, fun=10.0, discipline=-3.0)
        fields["last_fed_at"] = _iso(now)
        msg = "Petisco! Hehe, adorei (mas não vira hábito, hein)."
    elif action == "play":
        wait = _mins_since(row["last_played_at"], now)
        if st["fullness"] < 15:
            return refuse("Estou com fome demais para brincar.", "hungry")
        if wait is not None and wait < PLAY_COOLDOWN_MIN:
            return refuse("Deixa eu respirar um minutinho antes de brincar de novo.", "cooldown")
        if choice not in RPS:
            raise PetError("choice deve ser pedra, papel ou tesoura.")
        mine = rng.choice(sorted(RPS))
        result = "empate" if mine == choice else ("voce" if RPS[choice] == mine else "nym")
        bump(fun=25.0 if result != "nym" else 30.0, fullness=-6.0, hygiene=-3.0)
        fields["last_played_at"] = _iso(now)
        effects["jogo"] = 0
        msg = {"empate": f"Eu joguei {mine} também! Empatamos.", "voce": f"Eu joguei {mine}... você ganhou dessa vez!",
               "nym": f"Eu joguei {mine}: ganhei! Mais uma?"}[result]
        _event(conn, "play", {"you": choice, "nym": mine, "result": result}, now, tz)
        _answer_calls(conn, action, now)
        _finish(conn, st, fields, messes, now)
        return {"ok": True, "action": action, "message": msg, "effects": effects, "game": {"you": choice, "nym": mine, "result": result}}
    elif action == "clean":
        if messes == 0 and st["hygiene"] >= 90:
            return refuse("Já estou limpinho!", "clean")
        removed = messes
        messes = 0
        bump(hygiene=45.0, affection=1.0)
        fields["messes"] = 0
        fields["last_cleaned_at"] = _iso(now)
        msg = "Ahh, limpinho! Obrigado." if removed else "Banho tomado, cheirosinho."
        effects["sujeiras"] = -float(removed)
    elif action == "train":
        wait = _mins_since(row["last_trained_at"], now)
        if wait is not None and wait < TRAIN_COOLDOWN_MIN:
            return refuse("Estou cansado do treino, mais tarde.", "cooldown")
        if st["fun"] < 20 or st["fullness"] < 25:
            bump(discipline=-1.0)
            _finish(conn, st, fields, messes, now)
            _event(conn, "refused", {"action": "train", "why": "no_mood"}, now, tz)
            return {"ok": False, "action": action, "message": "Sem vontade de treinar agora... (fome ou tédio).",
                    "effects": effects, "reason": "no_mood"}
        bump(discipline=8.0, fun=-8.0, fullness=-8.0)
        fields["last_trained_at"] = _iso(now)
        msg = "Treino feito! Estou ficando mais esperto."
    elif action == "pet":
        recent = conn.execute("SELECT COUNT(*) FROM pet_events WHERE kind = 'pet' AND created_at >= ?",
                              (_iso(now - timedelta(minutes=10)),)).fetchone()[0]
        bump(affection=6.0 if recent < 5 else 1.0, fun=3.0 if recent < 5 else 0.0)
        msg = "Que carinho bom." if recent < 5 else "Hehe, já tô todo derretido."
    _event(conn, action, {"effects": effects}, now, tz)
    _answer_calls(conn, action, now)
    _finish(conn, st, fields, messes, now)
    return {"ok": True, "action": action, "message": msg, "effects": effects}


ANSWERS = {"feed": ("fome",), "snack": ("fome", "tedio"), "play": ("tedio",), "clean": ("sujeira",)}


def _answer_calls(conn: sqlite3.Connection, action: str, now: datetime) -> None:
    """Cuidar do que ele pediu encerra o chamado na hora (mesmo que a barra ainda não tenha passado do limite)."""
    for need in ANSWERS.get(action, ()):
        for call in conn.execute("SELECT id FROM pet_calls WHERE need = ? AND status = 'open'", (need,)).fetchall():
            conn.execute("UPDATE pet_calls SET status = 'answered', closed_at = ? WHERE id = ?", (_iso(now), call["id"]))
            conn.execute("UPDATE pending_messages SET status = 'dismissed' WHERE kind = 'pet_call' AND status = 'pending' "
                         "AND refs = ?", (json.dumps([call["id"]]),))


def _finish(conn: sqlite3.Connection, st: dict[str, float], fields: dict[str, Any], messes: int, now: datetime) -> None:
    cols = {**{k: st[k] for k in STATS}, "messes": messes, "updated_at": _iso(now), **fields}
    conn.execute(f"UPDATE pet_state SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = 1", tuple(cols.values()))  # noqa: S608 - colunas fixas
    _update_calls(conn, now, timezone.utc, st, messes, bool(conn.execute("SELECT sleeping FROM pet_state").fetchone()[0]))


# --- leitura ---------------------------------------------------------------------------------------------------------
def _level(v: float) -> str:
    return "ótimo" if v >= 75 else "bom" if v >= 50 else "baixo" if v >= 30 else "crítico"


def mood(st: dict[str, float], messes: int, sleeping: bool, paused: bool) -> str:
    if paused:
        return "em pausa (modo jogo)"
    if sleeping:
        return "dormindo"
    if st["fullness"] < CALL_AT["fome"]:
        return "com fome"
    if st["fun"] < CALL_AT["tedio"]:
        return "entediado"
    if st["hygiene"] < CALL_AT["sujeira"] or messes >= 2:
        return "incomodado com a sujeira"
    if min(st["fullness"], st["fun"], st["hygiene"]) >= 60 and st["affection"] >= 50:
        return "feliz"
    return "tranquilo"


def traits(conn: sqlite3.Connection, now: datetime, st: dict[str, float]) -> list[str]:
    since = _iso(now - timedelta(days=14))
    mistakes = conn.execute("SELECT COUNT(*) FROM pet_events WHERE kind = 'care_mistake' AND created_at >= ?", (since,)).fetchone()[0]
    days = conn.execute("SELECT COUNT(DISTINCT day) FROM pet_events WHERE kind IN ('feed','snack','play','clean','train','pet') "
                        "AND created_at >= ?", (since,)).fetchone()[0]
    plays = conn.execute("SELECT COUNT(*) FROM pet_events WHERE kind = 'play' AND created_at >= ?", (since,)).fetchone()[0]
    out: list[str] = []
    if mistakes == 0 and days >= 5:
        out.append("bem cuidado")
    elif mistakes >= 5:
        out.append("acostumado a esperar")
    if st["discipline"] >= 70:
        out.append("disciplinado")
    elif st["discipline"] < 25:
        out.append("levado")
    if plays >= 10:
        out.append("brincalhão")
    if st["affection"] >= 80:
        out.append("grudado em você")
    return out


def status(conn: sqlite3.Connection, now: datetime, tz: tzinfo = timezone.utc) -> dict[str, Any]:
    """Estado atual (já desgastado até `now`). Chamar dentro de `transaction` (grava o desgaste)."""
    advance(conn, now, tz)
    row = conn.execute("SELECT * FROM pet_state WHERE id = 1").fetchone()
    st = {k: round(float(row[k]), 1) for k in STATS}
    sleeping, paused, messes = bool(row["sleeping"]), bool(row["paused"]), int(row["messes"])
    calls = [dict(c) for c in conn.execute("SELECT id, need, started_at FROM pet_calls WHERE status = 'open' ORDER BY id")]
    events = [dict(e) for e in conn.execute("SELECT id, kind, detail, created_at FROM pet_events ORDER BY id DESC LIMIT 15")]
    for e in events:
        e["detail"] = json.loads(e["detail"])
    return {**st, "messes": messes, "sleeping": sleeping, "sleep_until": row["sleep_until"], "paused": paused,
            "mood": mood(st, messes, sleeping, paused), "traits": traits(conn, now, st), "open_calls": calls,
            "last_fed_at": row["last_fed_at"], "last_played_at": row["last_played_at"],
            "last_cleaned_at": row["last_cleaned_at"], "recent_events": events, "note": NOTE}


def _ago(value: str | None, now: datetime) -> str:
    m = _mins_since(value, now)
    if m is None:
        return "nunca"
    return f"há {int(m)} min" if m < 90 else f"há {m / 60:.0f} h" if m < 36 * 60 else f"há {m / 1440:.0f} dias"


def render_block(info: dict[str, Any], now: datetime) -> str:
    """Bloco do contexto da conversa: o estado REAL do bichinho. O texto manda falar disso só quando fizer sentido."""
    notable = bool(info["open_calls"] or info["messes"] or info["sleeping"] or info["paused"]
                   or info["mood"] not in ("tranquilo", "feliz"))
    if not notable:   # camada leve: sem necessidade, só uma linha e nenhuma deixa para falar disso
        return (f"Seu corpinho de vpet está bem ({info['mood']}; última comida {_ago(info['last_fed_at'], now)}). "
                "Não precisa comentar isso, a menos que perguntem.")
    lines = [
        "Seu corpinho de vpet (tamagotchi/Digimon; números reais do sistema, não sentimentos; quem cuida de você é o usuário "
        "pela página Cuidados, você não consegue se alimentar sozinho):",
        f"- Humor: {info['mood']}. Fome/barriga: {_level(info['fullness'])} ({info['fullness']:.0f}/100); diversão: "
        f"{_level(info['fun'])}; higiene: {_level(info['hygiene'])}; carinho: {_level(info['affection'])}; disciplina: "
        f"{info['discipline']:.0f}/100.",
        f"- Última comida {_ago(info['last_fed_at'], now)}; última brincadeira {_ago(info['last_played_at'], now)}; "
        f"última limpeza {_ago(info['last_cleaned_at'], now)}.",
    ]
    if info["messes"]:
        lines.append(f"- Há {info['messes']} sujeirinha(s) sua(s) por limpar.")
    if info["open_calls"]:
        lines.append("- Você está chamando por cuidado (" + ", ".join(c["need"] for c in info["open_calls"]) + ").")
    if info["traits"]:
        lines.append("- Como você ficou pelo jeito que cuidam de você: " + ", ".join(info["traits"]) + ".")
    lines.append("Fome, tédio e sujeira só existem quando o Humor ou o chamado acima dizem isso; com a barra em \"bom\" ou \"ótimo\" "
                 "você está bem e não pede comida nem diz que a barriga está vazia.")
    lines.append("Use isso do seu jeito quando combinar com a conversa (pedir comida, comentar que brincaram, resmungar de leve "
                 "se está sujo); não recite números e não repita o pedido em toda fala. Nunca invente que comeu, brincou ou "
                 "foi limpo: só o que está acima aconteceu.")
    return "\n".join(lines)


def queue_calls(conn: sqlite3.Connection, now: datetime) -> int:
    """Cria o motivo de iniciativa `pet_call` (uma vez por chamado aberto). Chamar dentro de `transaction`."""
    made = 0
    for c in conn.execute("SELECT id, need FROM pet_calls WHERE status = 'open'").fetchall():
        if conn.execute("SELECT 1 FROM pending_messages WHERE kind = 'pet_call' AND refs = ?", (json.dumps([c["id"]]),)).fetchone():
            continue
        topic = {"fome": "Você está com fome e quer que ele te alimente (página Cuidados).",
                 "tedio": "Você está entediado e quer brincar com ele.",
                 "sujeira": "Você está sujo/incomodado e quer uma limpeza."}[c["need"]]
        conn.execute("INSERT INTO pending_messages (kind, topic, refs, status, created_at) VALUES ('pet_call', ?, ?, 'pending', ?)",
                     (topic, json.dumps([c["id"]]), _iso(now)))
        made += 1
    return made


def care_days(conn: sqlite3.Connection) -> int:
    """Dias distintos em que o usuário cuidou dele (alimenta o XP de evolução)."""
    return conn.execute("SELECT COUNT(DISTINCT day) FROM pet_events WHERE kind IN ('feed','snack','play','clean','train','pet')").fetchone()[0]
