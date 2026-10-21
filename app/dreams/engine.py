"""Motor do Somnium: um ciclo de sonho = revisão real de memórias fora da conversa.

Etapas (registradas no diário): 1 seleção, 2 revisão, 3 associação, 4 consolidação, 5 exploração, 6 reflexão sobre a
identidade, 7 registro. Uma única chamada ao modelo cobre revisão, associações, hipóteses, perguntas e exploração
(respeita o limite de chamadas); o resto é determinístico e validado pelo código.

Garantias:
- Nada do que o modelo disser vale sem lastro: ids de memórias reais selecionadas (ver associations.py).
- Todas as alterações das etapas 3 a 5 entram numa ÚNICA transação: se algo falhar ou o ciclo for cancelado antes,
  as memórias originais ficam intactas.
- Um ciclo por vez (trava). Cancelamento e limite de duração são verificados entre as etapas; uma chamada ao modelo já
  em andamento só termina quando o Ollama responder.
- Hipóteses viram memórias *hipótese* (nunca fato). Propostas de identidade só passam pelo IdentityService (aprovação).
- Não implementado: limites de CPU/memória do processo; só limites de tempo, de chamadas ao modelo e de itens.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.config import Settings
from app.core.identity import IdentityError, IdentityService
from app.core.presence import add_candidate
from app.database import repository as repo
from app.database.connection import connect, transaction
from app.database.repository import log_audit, utcnow
from app.dreams import exploration
from app.dreams.associations import DreamResult, build_messages, parse_dream
from app.memory import store
from app.memory.extraction import ExtractionError
from app.memory.text import is_duplicate
from app.models.base import LLMProvider, ModelError

log = logging.getLogger("nym.dreams")

STALE_DAYS = 60
STALE_MAX_IMPORTANCE = 0.35
STALE_LIMIT = 20
MAX_NEW_EXPLORATIONS = 3


class Interrupted(Exception):
    pass


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


RECENT_CYCLES_FOR_ROTATION = 4   # memórias antigas sonhadas nos últimos ciclos vão para o fim da fila
MAX_OLD_MEMORIES = 12             # antigas por ciclo: o foco é o que é novo


def select_memories(conn: sqlite3.Connection, limit: int) -> tuple[list[dict[str, Any]], int]:
    """Etapa 1. Novidades desde o último sonho (até 70% do limite) + algumas memórias antigas, em RODÍZIO.

    "Desde o último sonho" conta a partir do FIM dele: o que o próprio ciclo gravou (hipóteses, fusões) não é novidade,
    senão cada sonho dispararia o seguinte.

    Rodízio (Fase 6.1): antes, as antigas eram sempre as "mais importantes", e cada sonho revia as mesmas 30 memórias e
    repetia o mesmo resumo. Agora as antigas que apareceram nos últimos ciclos vão para o fim da fila, e entram no
    máximo `MAX_OLD_MEMORIES`, então cada sonho revisita lembranças diferentes."""
    last = conn.execute("SELECT COALESCE(ended_at, started_at) AS ref FROM dream_cycles WHERE status IN ('completed', 'partial') "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    rows = [dict(r) for r in conn.execute("SELECT * FROM memories WHERE status = 'active'")]
    new = [r for r in rows if last is None or r["updated_at"] > last["ref"]]
    new.sort(key=lambda r: (-r["importance"], -r["id"]))
    chosen = new[: max(1, int(limit * 0.7))]
    taken = {r["id"] for r in chosen}
    recent_counts: dict[int, int] = {}
    for c in conn.execute("SELECT selected_ids FROM dream_cycles WHERE status IN ('completed', 'partial') ORDER BY id DESC LIMIT ?",
                          (RECENT_CYCLES_FOR_ROTATION,)):
        for mid in json.loads(c["selected_ids"]):
            recent_counts[mid] = recent_counts.get(mid, 0) + 1
    old = [r for r in rows if r["id"] not in taken]
    old.sort(key=lambda r: (recent_counts.get(r["id"], 0),
                            -(0.5 * r["importance"] + 0.3 * min(r["recurrence"], 5) / 5 + 0.2 * r["confidence"]), r["id"]))
    chosen += old[: max(0, min(limit - len(chosen), MAX_OLD_MEMORIES))]
    for r in chosen:
        r["is_new"] = r["id"] in {n["id"] for n in new}
    return sorted(chosen, key=lambda r: r["id"]), len(new)


def previous_dreams(conn: sqlite3.Connection, limit: int = 3) -> list[str]:
    """O que já foi sonhado recentemente (revisões e hipóteses), para o próximo sonho não repetir."""
    out = []
    for c in conn.execute("SELECT id, review FROM dream_cycles WHERE status IN ('completed', 'partial') AND review IS NOT NULL "
                          "ORDER BY id DESC LIMIT ?", (limit,)):
        out.append(c["review"])
        out += [f"(hipótese) {r['content']}" for r in conn.execute(
            "SELECT content FROM dream_items WHERE cycle_id = ? AND kind IN ('hypothesis', 'association') ORDER BY id LIMIT 4", (c["id"],))]
    return out


class DreamEngine:
    def __init__(self, settings: Settings, provider: LLMProvider, identity: IdentityService,
                 clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._provider = provider
        self._identity = identity
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._cancel = threading.Event()

    # --- controle -----------------------------------------------------------------------------------
    def is_running(self) -> bool:
        return self._lock.locked()

    def cancel(self) -> bool:
        if self.is_running():
            self._cancel.set()
            return True
        return False

    def _checkpoint(self, started: datetime) -> None:
        if self._cancel.is_set():
            raise Interrupted("cancelado pelo usuário")
        if (self._clock() - started).total_seconds() > self._settings.dream_max_seconds:
            raise Interrupted("limite de duração do ciclo atingido")

    # --- ciclo ----------------------------------------------------------------------------------------
    def run_cycle(self, trigger: str = "manual", reason: str | None = None) -> dict[str, Any]:
        if trigger not in ("manual", "idle", "scheduled"):
            raise ValueError(f"Gatilho inválido: {trigger!r}")
        if not self._lock.acquire(blocking=False):
            return {"status": "busy"}
        self._cancel.clear()
        conn = connect(self._settings.db_path)
        try:
            return self._run(conn, trigger, reason)
        finally:
            conn.close()
            self._lock.release()

    def _run(self, conn: sqlite3.Connection, trigger: str, reason: str | None) -> dict[str, Any]:
        started = self._clock()
        selected, new_count = select_memories(conn, self._settings.dream_max_memories)
        if not selected:
            return {"status": "skipped", "reason": "não há memórias para revisar"}
        if trigger != "manual" and new_count == 0:
            return {"status": "skipped", "reason": "nada de novo desde o último sonho"}

        stages: list[dict[str, Any]] = []
        with transaction(conn):
            cur = conn.execute(
                "INSERT INTO dream_cycles (trigger, status, reason, started_at, selected_ids, model) VALUES (?, 'running', ?, ?, ?, ?)",
                (trigger, reason, utcnow(), json.dumps([m["id"] for m in selected]), self._provider.model))
            cycle_id = int(cur.lastrowid)
        model_calls = prompt_chars = 0
        status, error, review = "failed", None, None
        apply_mark: int | None = None
        applied = False

        def stage(name: str, state: str, detail: str = "") -> None:
            stages.append({"stage": name, "status": state, "detail": detail, "at": utcnow()})
            conn.execute("UPDATE dream_cycles SET stage_log = ? WHERE id = ?", (json.dumps(stages, ensure_ascii=False), cycle_id))

        try:
            stage("selecao", "ok", f"{len(selected)} memórias ({new_count} novas)")
            self._checkpoint(started)

            if self._settings.dream_max_model_calls < 1:
                raise ModelError("limite de chamadas ao modelo é zero")
            open_q = [r["content"] for r in conn.execute("SELECT content FROM dream_items WHERE kind = 'open_question' AND status = 'open'")]
            messages = build_messages(selected, open_q, previous_dreams(conn))
            prompt_chars = sum(len(m.content) for m in messages)
            response = self._provider.chat(messages, temperature=0.4, json_mode=True)
            model_calls += 1
            result = parse_dream(response.content, {m["id"] for m in selected})
            review = result.review
            stage("revisao", "ok", f"{len(review)} caracteres")
            self._checkpoint(started)

            apply_mark = len(stages)
            with transaction(conn):   # etapas 3 a 5: tudo ou nada
                self._apply(conn, cycle_id, result, selected, started, stage)
            applied = True

            status = "completed"
            problems = self._reflect_on_identity(conn, cycle_id, stage)
            if problems:
                status = "partial"
        except Interrupted as exc:
            status, error = "interrupted", str(exc)
            self._forget_unapplied(stages, apply_mark, applied)
            stage("interrompido", "interrompido", error)
        except (ModelError, ExtractionError) as exc:
            status, error = "failed", str(exc)
            self._forget_unapplied(stages, apply_mark, applied)
            stage("falha", "erro", error)
        except Exception as exc:  # noqa: BLE001 - qualquer erro vira falha registrada; nada parcial foi gravado
            log.exception("Ciclo de sonho falhou")
            status, error = "failed", f"{type(exc).__name__}: {exc}"
            self._forget_unapplied(stages, apply_mark, applied)
            stage("falha", "erro", error)

        ended = self._clock()
        with transaction(conn):
            stage("registro", "ok", status)
            conn.execute("UPDATE dream_cycles SET status = ?, ended_at = ?, review = ?, model_calls = ?, prompt_chars = ?, "
                         "duration_ms = ?, error = ? WHERE id = ?",
                         (status, utcnow(), review, model_calls, prompt_chars,
                          int((ended - started).total_seconds() * 1000), error, cycle_id))
            log_audit(conn, "dream.cycle", {"cycle_id": cycle_id, "status": status, "trigger": trigger})
        return {"status": status, "cycle_id": cycle_id, "error": error}

    @staticmethod
    def _forget_unapplied(stages: list[dict[str, Any]], mark: int | None, applied: bool) -> None:
        """Etapas 3 a 5 desfeitas por rollback não podem aparecer como feitas no diário."""
        if mark is not None and not applied:
            del stages[mark:]

    # --- etapas 3 a 5 (dentro da transação) ------------------------------------------------------------
    def _apply(self, conn: sqlite3.Connection, cycle_id: int, result: DreamResult, selected: list[dict[str, Any]],
               started: datetime, stage: Callable[..., None]) -> dict[str, int]:
        def item(kind: str, content: str, refs: list[int], confidence: float | None = None, status: str | None = None) -> None:
            conn.execute("INSERT INTO dream_items (cycle_id, kind, content, refs, confidence, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (cycle_id, kind, content, json.dumps(refs), confidence, status, utcnow()))

        # 3. associação
        for a in result.associations:
            item("association", a.text, a.ids, a.confidence)
            ids = a.ids[:3]
            for i, x in enumerate(ids):
                for y in ids[i + 1:]:
                    store.add_link(conn, x, y, "related")
        stage("associacao", "ok", f"{len(result.associations)} ligações")

        # 4. consolidação: duplicatas, hipóteses (como hipótese!), perguntas, arquivamento de baixa utilidade
        merges = store.dedupe_batch(conn, dry_run=False)
        if merges:
            item("consolidation", f"{len(merges)} duplicata(s) mesclada(s)", [x for m in merges for x in (m["keep"], m["drop"])])
        new_h = 0
        for h in result.hypotheses:
            dup = store.find_similar(conn, h.text, "semantic")
            if dup is not None:
                item("hypothesis", h.text, [dup["id"], *h.ids], h.confidence)   # já existe algo parecido: só registra no diário
                continue
            mid = store.create_memory(conn, kind="semantic", content=h.text, epistemic="hypothesis", origin="nym_inferred",
                                      importance=0.4, confidence=h.confidence, actor="nym", reason=f"sonho #{cycle_id}")
            for evidence in h.ids:
                store.add_link(conn, evidence, mid, "supports")
            item("hypothesis", h.text, [mid, *h.ids], h.confidence)
            new_h += 1
        existing_q = [r["content"] for r in conn.execute("SELECT content FROM dream_items WHERE kind = 'open_question' AND status = 'open'")]
        for q in result.open_questions:
            if not any(is_duplicate(q.text, e) for e in existing_q):
                item("open_question", q.text, q.ids, status="open")
                existing_q.append(q.text)
        archived = self._archive_stale(conn, cycle_id, started)
        if archived:
            item("archive", f"{len(archived)} lembrança(s) de baixa utilidade arquivada(s) (reversível)", archived)
        stage("consolidacao", "ok", f"{new_h} hipóteses novas, {len(merges)} fusões, {len(archived)} arquivadas")

        # 5. exploração: só registra tarefas, com limites (a execução é das Fases 5 e 6)
        created = 0
        for e in result.exploration:
            if exploration.add_task(conn, cycle_id, e.question, e.why, e.ids, min(MAX_NEW_EXPLORATIONS, self._settings.dream_max_memories), created):
                created += 1
        if result.dream_note:
            text, ids = result.dream_note
            item("dream_note", text, ids)
            add_candidate(conn, "dream_note", text, ids, cycle_id)
        stage("exploracao", "ok", f"{created} tarefas de exploração")
        return {"hypotheses": new_h, "explorations": created}

    def _archive_stale(self, conn: sqlite3.Connection, cycle_id: int, now: datetime) -> list[int]:
        cutoff = (now - timedelta(days=STALE_DAYS)).isoformat(timespec="milliseconds")
        rows = conn.execute(
            "SELECT id FROM memories WHERE kind = 'episodic' AND status = 'active' AND occurred_at < ? AND importance < ? "
            "AND access_count = 0 AND recurrence = 1 ORDER BY occurred_at LIMIT ?", (cutoff, STALE_MAX_IMPORTANCE, STALE_LIMIT)).fetchall()
        for r in rows:
            store.set_status(conn, r["id"], "archived", "system", f"baixa utilidade (sonho #{cycle_id})")
        return [r["id"] for r in rows]

    # --- etapa 6 (depois da transação: o IdentityService usa a própria conexão) ---------------------------
    def _reflect_on_identity(self, conn: sqlite3.Connection, cycle_id: int, stage: Callable[..., None]) -> bool:
        """Observações repetidas em episódios diferentes viram PROPOSTAS (nunca mudanças diretas). Retorna True se houve falha."""
        proposed, failed = 0, False
        for cand in store.recurring_identity_candidates(conn):
            done = conn.execute("SELECT 1 FROM dream_items i, json_each(i.refs) j WHERE i.kind = 'identity_proposal' AND j.value = ?", (cand["id"],)).fetchone()
            if done:
                continue
            try:
                change = self._identity.propose(
                    kind="preference", action="add", value=cand["content"][:900],
                    justification=f"Observado {cand['recurrence']} vezes em {cand['episodes']} episódios diferentes (sonho #{cycle_id}).",
                    confidence=min(cand["confidence"], 0.7), related_memory_ids=[cand["id"]])
            except IdentityError as exc:   # proposta repetida, valor inválido... não é falha do ciclo
                log.info("Proposta de identidade ignorada: %s", exc)
                continue
            except Exception:  # noqa: BLE001
                log.exception("Falha ao propor mudança de identidade")
                failed = True
                continue
            with transaction(conn):
                conn.execute("INSERT INTO dream_items (cycle_id, kind, content, refs, status, created_at) VALUES (?, 'identity_proposal', ?, ?, NULL, ?)",
                             (cycle_id, f"proposta #{change['id']}: {cand['content']}", json.dumps([cand["id"]]), utcnow()))
            proposed += 1
        stage("reflexao_identidade", "erro" if failed else "ok", f"{proposed} proposta(s)")
        return failed
