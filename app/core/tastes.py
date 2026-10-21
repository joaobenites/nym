"""Gostos e prioridades próprios do Nym (Fase 7.1).

Um amigo tem gosto: certas coisas o puxam, outras o cansam, e isso aparece nas conversas e no que ele resolve fazer.
Aqui o gosto NÃO é texto inventado pelo modelo: o código recalcula tudo a partir de registros que existem de verdade.

Fontes de evidência (cada registro conta uma vez por assunto):
- pesquisa   : buscas que ele fez (mais peso quando o objetivo era dele)
- criacao    : desenhos, textos, brinquedos que ele criou
- objetivo   : objetivos que ele mesmo criou (e terminou)
- sonho      : ligações e hipóteses dos sonhos
- conversa   : assuntos que vocês trazem com frequência (peso pequeno: é gosto "emprestado" de você)
- reacao     : você curtiu (+) ou não curtiu (-) uma criação dele
- frustracao : objetivos dele que não terminaram (-)

Regras (todas no código; o modelo nunca decide um gosto):
- precisa de volume, de pelo menos 2 dias diferentes e de pelo menos 2 tipos de fonte para virar gosto;
- depois de formado, se mantém com um limiar menor (histerese): uma conversa sozinha não faz o gosto oscilar;
- as evidências perdem peso com o tempo (meia-vida de 21 dias): o gosto esmaece se deixa de ser alimentado;
- não curtir exige pelo menos uma reação sua (não curtiu) mais reações/frustrações repetidas: objetivo que não terminou
  sozinho nunca vira "não gosto" (a falha costuma ser da ferramenta ou do modelo, não do assunto);
- cada tipo de fonte tem um teto de peso por assunto: os sonhos, que se repetem muito, não formam um gosto sozinhos;
- o usuário pode vetar um gosto: ele nunca mais é formado;
- evidência que parece segredo é descartada; evidência cujo registro foi apagado some no próximo recálculo.

Isso é um modelo calculado de preferência, não prova de sentimento; a interface e o prompt dizem isso.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.memory.text import STOPWORDS, looks_sensitive, normalize

NOTE = "Preferência calculada de registros reais; não é prova de sentimento."
HALF_LIFE_DAYS = 21.0
MIN_LEN = 5
ACTIVATE_SCORE = 3.0        # curtir: soma mínima para formar
KEEP_SCORE = 1.8            # curtir: soma mínima para continuar (histerese)
ACTIVATE_NEG = 2.0          # não curtir: soma mínima (em módulo) para formar
KEEP_NEG = 1.2
MIN_ITEMS = 3
MIN_ITEMS_NEG = 2
MIN_DAYS = 2
MIN_SOURCES = 2
STRONG_SOURCES = 4          # recorrência em 4+ tipos de fonte vale mesmo num dia só (convivência muito recente)
STRONG_ITEMS = 10
SOURCE_CAP = {"conversa": 2.0}   # teto do peso de uma fonte por assunto (padrão abaixo)
DEFAULT_CAP = 3.0
STRENGTH_FULL = 10.0        # soma que equivale a força 1.0
REFRESH_STATE_KEY = "tastes_refreshed_at"
MAX_USER_MESSAGES = 400
MAX_EVIDENCE_KEPT = 8
SHARE_COOLDOWN_DAYS = 5     # não puxa assunto do mesmo gosto de novo antes disso

SOURCE_LABEL = {
    "pesquisa": "pesquisou", "criacao": "criou", "objetivo": "decidiu fazer", "sonho": "sonhou",
    "conversa": "conversaram", "reacao": "você reagiu", "frustracao": "não deu certo",
}
OWN_SOURCES = frozenset({"pesquisa", "criacao", "objetivo", "sonho", "reacao", "frustracao"})

# Palavras comuns que não são "assunto" (além das stopwords gerais).
GENERIC = frozenset(normalize(w) for w in """
fazer feito feita criar sobre notas nota quero queria vamos podemos algo coisa coisas ainda assim tambem pode podem ficar
fiquei estou esta estao porque entao agora depois antes hoje ontem amanha aqui nada tudo todo toda todos todas onde qual
quais pouco mesmo mesma outro outra outros outras forma jeito vezes tipo tinha tenho tinha achei acho gosto gostei vou vai
vimos pesquisar pesquisa pesquisas descobrir descobri resultado resultados registro registros usuario usuaria conversa
conversas conversar conversando falar falei falou disse dizer saber sabe sabia quer queres querer fazendo feito
pergunta perguntas pergunte perguntar ideia ideias ver vendo visto lembrar lembranca lembrancas tarefa tarefas objetivo
objetivos sonho sonhos sonhar sonhei mensagem mensagens texto textos nome nomes parte partes momento tempo cada junto
algum alguma alguns algumas qualquer sempre nunca ainda apenas somente tanto tanta quanto quanta bastante demais muito
muita muitos muitas melhor melhores pior maior menor novo nova novos novas velho velha primeiro primeira segundo segunda
depois entre durante desde ate contra sendo estar estava estive fosse seria seriam serao existe existem precisa precisar
preciso usar usando usado serve ajuda ajudar ajudou comecar comecei terminar terminei final inicio exemplo exemplos
poderia deveria devemos deve devem esteja talvez proprio propria proprios proprias sistema sistemas processo processos
relacao relacoes conceito conceitos interacao interacoes reflexao reflexoes percepcao experimento experimentos desenvolvimento
interesse interesses desejo desejos buscando explorar investigar escrever completar entender possivel possiveis importante
importantes diferente diferentes mesmo humano humanos
""".split())

_WORD = re.compile(r"[a-zA-ZÀ-ÿ]+")


@dataclass
class Evidence:
    key: str
    label: str
    source: str
    ref: int
    weight: float          # com sinal: positivo curte, negativo não curte
    at: datetime
    snippet: str


@dataclass
class TasteCandidate:
    key: str
    label: str
    valence: int
    strength: float
    score: float
    items: int
    days: int
    sources: dict[str, int]
    origin: str
    first_seen: datetime
    last_seen: datetime
    evidence: list[Evidence] = field(default_factory=list)


def _parse(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def topic_key(word: str) -> str:
    token = normalize(word)
    if len(token) > 4 and token.endswith("s"):
        token = token[:-1]
    return token[:6]


def _is_verbish(norm: str) -> bool:
    """Gerúndio, infinitivo e advérbio -mente não são assunto."""
    return len(norm) >= 6 and norm.endswith(("ando", "endo", "indo", "ar", "er", "ir", "mente"))


def topics_in(text: str) -> dict[str, str]:
    """{chave: palavra como escrita} dos assuntos de um texto (sem repetição)."""
    found: dict[str, str] = {}
    for raw in _WORD.findall(text or ""):
        norm = normalize(raw)
        if len(norm) < MIN_LEN or norm in STOPWORDS or norm in GENERIC or " " in norm or _is_verbish(norm):
            continue
        found.setdefault(topic_key(raw), raw.lower())
    return found


def _decay(at: datetime, now: datetime) -> float:
    age_days = max(0.0, (now - at).total_seconds() / 86400)
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


def _snippet(text: str) -> str:
    return " ".join((text or "").split())[:90]


def _collect(conn: sqlite3.Connection) -> list[Evidence]:
    """Todos os registros reais que podem sustentar um gosto, já separados por assunto."""
    out: list[Evidence] = []

    def add(source: str, ref: int, weight: float, when: str | None, text: str, extra: str = "") -> None:
        if looks_sensitive(text):
            return
        at = _parse(when)
        for key, label in topics_in(f"{text} {extra}").items():
            out.append(Evidence(key, label, source, ref, weight, at, _snippet(text)))

    for r in conn.execute(
            "SELECT r.id, r.query, r.created_at, g.origin AS goal_origin FROM research_log r "
            "LEFT JOIN goals g ON g.id = r.goal_id WHERE r.kind = 'search' AND r.status IN ('ok', 'cached')"):
        add("pesquisa", r["id"], 1.0 if r["goal_origin"] == "nym" else 0.6, r["created_at"], r["query"])

    reactions = {r["target_id"]: r["value"] for r in conn.execute("SELECT target_id, value FROM feedback WHERE target = 'creation'")}
    for r in conn.execute("SELECT id, title, about, created_at FROM creations WHERE status != 'archived'"):
        value = reactions.get(r["id"])
        if value is None or value > 0:
            add("criacao", r["id"], 1.5, r["created_at"], r["title"], r["about"] or "")
        if value is not None:       # criação que ele não curtiu (ou que você não curtiu) não conta como "gosto" de fazer
            add("reacao", r["id"], 1.5 if value > 0 else -2.0, r["created_at"], r["title"], r["about"] or "")

    for r in conn.execute("SELECT id, description, status, last_error, created_at, updated_at FROM goals WHERE origin = 'nym'"):
        if r["status"] == "done":
            add("objetivo", r["id"], 1.5, r["updated_at"], r["description"])
        elif r["status"] in ("active", "proposed"):
            add("objetivo", r["id"], 0.8, r["created_at"], r["description"])
        elif r["status"] in ("paused", "cancelled") and r["last_error"]:
            add("frustracao", r["id"], -0.5, r["updated_at"], r["description"])

    for r in conn.execute("SELECT id, content, created_at FROM dream_items WHERE kind IN ('association', 'hypothesis')"):
        add("sonho", r["id"], 0.7, r["created_at"], r["content"])

    main = conn.execute("SELECT value FROM app_state WHERE key = 'main_conversation_id'").fetchone()
    if main:
        for r in conn.execute("SELECT id, content, created_at FROM messages WHERE conversation_id = ? AND role = 'user' "
                              "ORDER BY id DESC LIMIT ?", (main["value"], MAX_USER_MESSAGES)):
            add("conversa", r["id"], 0.4, r["created_at"], r["content"])
    return out


def _capped(evs: Sequence[Evidence], positive: bool, now: datetime) -> float:
    total: dict[str, float] = defaultdict(float)
    for e in evs:
        if (e.weight > 0) == positive:
            total[e.source] += abs(e.weight) * _decay(e.at, now)
    return sum(min(v, SOURCE_CAP.get(src, DEFAULT_CAP)) for src, v in total.items())


def compute(conn: sqlite3.Connection, now: datetime, keep_keys: Mapping[str, int] | None = None) -> list[TasteCandidate]:
    """Gostos que as evidências sustentam agora. `keep_keys` = {chave: valência} dos já formados (limiar menor)."""
    keep_keys = keep_keys or {}
    by_key: dict[str, list[Evidence]] = defaultdict(list)
    seen: set[tuple[str, str, int]] = set()
    for ev in _collect(conn):
        ident = (ev.key, ev.source, ev.ref)
        if ident in seen:      # o mesmo registro nunca conta duas vezes para o mesmo assunto
            continue
        seen.add(ident)
        by_key[ev.key].append(ev)

    result: list[TasteCandidate] = []
    for key, evs in by_key.items():
        pos = _capped(evs, positive=True, now=now)
        neg = _capped(evs, positive=False, now=now)
        net = pos - neg
        was = keep_keys.get(key)
        if net >= 0:
            valence, score = 1, net
            need = KEEP_SCORE if was == 1 else ACTIVATE_SCORE
            items = [e for e in evs if e.weight > 0]
            need_items = MIN_ITEMS
        else:
            valence, score = -1, -net
            need = KEEP_NEG if was == -1 else ACTIVATE_NEG
            items = [e for e in evs if e.weight < 0]
            need_items = MIN_ITEMS_NEG
        if score < need or len(items) < need_items:
            continue
        days = {e.at.date().isoformat() for e in items}
        sources = Counter(e.source for e in items)
        if valence == 1 and not was:
            steady = len(days) >= MIN_DAYS and len(sources) >= MIN_SOURCES
            intense = len(sources) >= STRONG_SOURCES and len(items) >= STRONG_ITEMS
            if not (steady or intense):
                continue
        if valence == -1 and not was and "reacao" not in sources:
            continue
        own_weight = sum(abs(e.weight) for e in items if e.source in OWN_SOURCES)
        total_weight = sum(abs(e.weight) for e in items) or 1.0
        label = Counter(e.label for e in items).most_common(1)[0][0]
        result.append(TasteCandidate(
            key=key, label=label, valence=valence, strength=round(min(1.0, score / STRENGTH_FULL), 3), score=round(score, 3),
            items=len(items), days=len(days), sources=dict(sources),
            origin="own" if own_weight / total_weight >= 0.5 else "shared",
            first_seen=min(e.at for e in items), last_seen=max(e.at for e in items),
            evidence=sorted(items, key=lambda e: abs(e.weight) * _decay(e.at, now), reverse=True)[:MAX_EVIDENCE_KEPT]))
    result.sort(key=lambda c: (-c.strength, c.key))
    return result


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def refresh(conn: sqlite3.Connection, now: datetime) -> dict[str, int]:
    """Recalcula e grava os gostos. Chamar dentro de `transaction(conn)`. Vetados nunca voltam."""
    existing = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM tastes")}
    vetoed = {k for k, r in existing.items() if r["status"] == "vetoed"}
    keep = {k: r["valence"] for k, r in existing.items() if r["status"] == "active"}
    found = {c.key: c for c in compute(conn, now, keep) if c.key not in vetoed}
    stamp = _iso(now)
    created = updated = faded = 0
    for key, c in found.items():
        row = existing.get(key)
        sources = json.dumps(c.sources, ensure_ascii=False)
        if row is None:
            cur = conn.execute(
                "INSERT INTO tastes (key, label, valence, strength, score, items, days, sources, origin, status, first_seen, "
                "last_seen, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                (key, c.label, c.valence, c.strength, c.score, c.items, c.days, sources, c.origin, _iso(c.first_seen),
                 _iso(c.last_seen), stamp))
            taste_id = int(cur.lastrowid)
            created += 1
        else:
            taste_id = row["id"]
            conn.execute(
                "UPDATE tastes SET label = ?, valence = ?, strength = ?, score = ?, items = ?, days = ?, sources = ?, origin = ?, "
                "status = 'active', first_seen = ?, last_seen = ?, updated_at = ? WHERE id = ?",
                (c.label, c.valence, c.strength, c.score, c.items, c.days, sources, c.origin,
                 row["first_seen"] if row["valence"] == c.valence else _iso(c.first_seen), _iso(c.last_seen), stamp, taste_id))
            updated += 1
        conn.execute("DELETE FROM taste_evidence WHERE taste_id = ?", (taste_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO taste_evidence (taste_id, source, ref, weight, day, snippet) VALUES (?, ?, ?, ?, ?, ?)",
            [(taste_id, e.source, e.ref, e.weight, e.at.date().isoformat(), e.snippet) for e in c.evidence])
    for key, row in existing.items():
        if row["status"] == "active" and key not in found:
            conn.execute("UPDATE tastes SET status = 'faded', strength = 0, updated_at = ? WHERE id = ?", (stamp, row["id"]))
            faded += 1
    conn.execute("INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                 (REFRESH_STATE_KEY, stamp, stamp))
    return {"created": created, "updated": updated, "faded": faded}


def is_stale(conn: sqlite3.Connection, now: datetime, minutes: float = 20) -> bool:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (REFRESH_STATE_KEY,)).fetchone()
    return row is None or (now - _parse(row["value"])).total_seconds() >= minutes * 60


def active(conn: sqlite3.Connection, limit: int = 12) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM tastes WHERE status = 'active' ORDER BY strength DESC, id LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["sources"] = json.loads(d["sources"] or "{}")
        out.append(d)
    return out


def with_evidence(conn: sqlite3.Connection, status: str | None = None, limit: int = 60) -> list[dict[str, Any]]:
    """Para a interface: cada gosto com as evidências que o sustentam."""
    q = "SELECT * FROM tastes" + (" WHERE status = ?" if status else "") + " ORDER BY status, strength DESC, id LIMIT ?"
    rows = conn.execute(q, ((status, limit) if status else (limit,))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["sources"] = json.loads(d["sources"] or "{}")
        d["evidence"] = [dict(e) for e in conn.execute(
            "SELECT source, ref, weight, day, snippet FROM taste_evidence WHERE taste_id = ? ORDER BY ABS(weight) DESC, id LIMIT ?",
            (d["id"], MAX_EVIDENCE_KEPT))]
        out.append(d)
    return out


class TasteError(ValueError):
    pass


def set_status(conn: sqlite3.Connection, taste_id: int, status: str, now: datetime) -> dict[str, Any]:
    """Só a API do usuário chama: vetar (nunca mais formado) ou restaurar (volta a ser recalculado)."""
    if status not in ("vetoed", "faded"):
        raise TasteError("Estado inválido.")
    row = conn.execute("SELECT id, status FROM tastes WHERE id = ?", (taste_id,)).fetchone()
    if row is None:
        raise TasteError("Gosto não encontrado.")
    conn.execute("UPDATE tastes SET status = ?, strength = 0, updated_at = ? WHERE id = ?", (status, _iso(now), taste_id))
    return {"id": taste_id, "status": status}


MIN_COMMON_CHAT = 3          # falas do dono sobre o assunto para contar como "vocês dois"
MIN_COMMON_OWN = 3           # registros dele (pesquisa, criação, objetivo, sonho) sobre o mesmo assunto


def common_ground(tastes: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Gostos curtidos que os DOIS alimentam: o dono traz o assunto (conversa) e o Nym também tem registros próprios dele
    (pesquisa, criação, objetivo, sonho). É o "a gente curte isso" de uma amizade, calculado só de registros."""
    out = []
    for t in tastes:
        if t["valence"] <= 0:
            continue
        srcs = t["sources"] if isinstance(t["sources"], dict) else json.loads(t["sources"] or "{}")
        own = sum(n for s, n in srcs.items() if s in OWN_SOURCES and s not in ("reacao", "frustracao"))
        if srcs.get("conversa", 0) >= MIN_COMMON_CHAT and own >= MIN_COMMON_OWN:
            out.append(t)
    return out


def render_block(tastes: Sequence[Mapping[str, Any]]) -> str:
    """Bloco de contexto do chat (vai junto da última fala, nunca no prompt de sistema)."""
    likes = [t for t in tastes if t["valence"] > 0][:5]
    dislikes = [t for t in tastes if t["valence"] < 0][:3]
    if not likes and not dislikes:
        return ("Seus gostos: ainda não se formaram o suficiente. Se perguntarem do que você gosta, diga que está descobrindo "
                "e não invente favoritos.")

    def why(t: Mapping[str, Any]) -> str:
        srcs = t["sources"] if isinstance(t["sources"], dict) else json.loads(t["sources"] or "{}")
        verbs = ", ".join(SOURCE_LABEL[s] for s, _ in sorted(srcs.items(), key=lambda kv: -kv[1])[:3] if s in SOURCE_LABEL)
        kind = "por conta própria" if t["origin"] == "own" else "porque vocês falam disso"
        return f"{t['label']} ({kind}; {verbs})"

    lines = ["Seus gostos (calculados do que você pesquisou, criou, sonhou e conversou; é só isso, não invente outros):"]
    if likes:
        lines.append("- curte: " + "; ".join(why(t) for t in likes))
    if dislikes:
        lines.append("- não curte: " + "; ".join(why(t) for t in dislikes))
    common = common_ground(tastes)
    if common:
        lines.append("- vocês dois curtem (ele traz o assunto e você também foi atrás por conta própria): "
                     + "; ".join(t["label"] for t in common[:3])
                     + ". Dá para tratar como assunto de dupla: opinião sua, algo que você viu ou fez, sem inventar o que não está nos registros.")
    lines.append("Quando o assunto tocar nisso, deixe a preferência aparecer com naturalidade e opinião própria, sem recitar a lista. "
                 "Se perguntarem do que você gosta, responda com estes.")
    return "\n".join(lines)


def affinity(text: str, tastes: Sequence[Mapping[str, Any]]) -> float:
    """-1..1: quanto um texto combina com os gostos (a maior força entre os assuntos que coincidem, com sinal)."""
    keys = set(topics_in(text))
    best = 0.0
    for t in tastes:
        if t["key"] in keys and abs(t["valence"] * t["strength"]) > abs(best):
            best = t["valence"] * t["strength"]
    return round(best, 3)


def adjust_priority(priority: int, text: str, tastes: Sequence[Mapping[str, Any]]) -> tuple[int, float]:
    """Prioridade própria: 1 = mais urgente. O que ele curte sobe, o que ele não curte desce (e a razão fica registrada)."""
    score = affinity(text, tastes)
    if score >= 0.3:
        return max(1, priority - 1), score
    if score <= -0.3:
        return min(5, priority + 1), score
    return priority, score


def pick_to_share(conn: sqlite3.Connection, now: datetime) -> dict[str, Any] | None:
    """Gosto 'dele' (own, curtido, forte) sobre o qual ele ainda não puxou assunto recentemente."""
    for t in active(conn, limit=20):
        if t["valence"] < 0 or t["origin"] != "own" or t["strength"] < 0.35:
            continue
        if t["shared_at"] and (now - _parse(t["shared_at"])).total_seconds() < SHARE_COOLDOWN_DAYS * 86400:
            continue
        return t
    return None


def share_topic(conn: sqlite3.Connection, taste: Mapping[str, Any]) -> str:
    ev = conn.execute("SELECT source, snippet FROM taste_evidence WHERE taste_id = ? AND weight > 0 ORDER BY weight DESC, id LIMIT 3",
                      (taste["id"],)).fetchall()
    facts = "; ".join(f"{SOURCE_LABEL.get(e['source'], e['source'])}: {e['snippet']}" for e in ev)
    return (f"Você anda curtindo \"{taste['label']}\" (registros: {facts}). Puxe assunto sobre isso como um amigo que "
            "lembrou de algo de que gosta: compartilhe algo seu, curto, só com o que está nos registros.")
