"""Evolução do Nym (Fase 6.2): estágios de convivência calculados de dados REAIS, no espírito de um vpet.

Nada aqui é sorteado nem inventado: a experiência vem de dias em que vocês conversaram, mensagens suas, objetivos
concluídos, sonhos e lembranças confirmadas. O estágio só sobe (a história não se perde). O avatar da interface
muda de forma conforme o estágio. Os nomes dos estágios são próprios do Nym.
"""
from __future__ import annotations

import sqlite3
from typing import Any

STAGES: tuple[tuple[int, str, str], ...] = (
    (0, "Ovo digital", "acabou de chegar"),
    (40, "Filhote", "começando a conhecer você"),
    (200, "Aprendiz", "já tem uma rotina com você"),
    (600, "Parceiro", "faz coisas por conta própria e conhece bem você"),
    (1500, "Companheiro", "uma convivência longa e cheia de história"),
)
WEIGHTS = {"days": 15, "user_messages": 1, "goals_done": 10, "dreams": 5, "confirmed_memories": 2, "creations": 3,
           "lessons": 4, "perceptions": 2, "care_days": 6}
STAGE_KEY = "growth_stage"


def compute(conn: sqlite3.Connection) -> dict[str, Any]:
    main = conn.execute("SELECT value FROM app_state WHERE key = 'main_conversation_id'").fetchone()
    cid = main["value"] if main else ""
    counts = {
        "days": conn.execute("SELECT COUNT(DISTINCT substr(created_at, 1, 10)) FROM messages WHERE conversation_id = ? "
                             "AND role = 'user'", (cid,)).fetchone()[0],
        "user_messages": conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND role = 'user'",
                                      (cid,)).fetchone()[0],
        "goals_done": conn.execute("SELECT COUNT(*) FROM goals WHERE mode = 'task' AND status = 'done'").fetchone()[0],
        "dreams": conn.execute("SELECT COUNT(*) FROM dream_cycles WHERE status IN ('completed', 'partial')").fetchone()[0],
        "confirmed_memories": conn.execute("SELECT COUNT(*) FROM memories WHERE status = 'active' AND epistemic = 'confirmed'")
                                  .fetchone()[0],
        # Fase 7: criações (todas as versões contam: refazer melhor é crescer) e lições que ainda valem
        "creations": conn.execute("SELECT COUNT(*) FROM creations WHERE status != 'archived'").fetchone()[0],
        "lessons": conn.execute("SELECT COUNT(*) FROM lessons WHERE status = 'active'").fetchone()[0],
        # Fase 8: coisas que ele viu ou assistiu de verdade (falhas de visão não contam)
        "perceptions": conn.execute("SELECT COUNT(*) FROM perceptions WHERE description NOT LIKE 'Não consegui enxergar%'")
                           .fetchone()[0],
    }
    try:   # Fase 10: dias em que o usuário cuidou do corpinho de vpet (alimentar, brincar, limpar, treinar, carinho)
        counts["care_days"] = conn.execute("SELECT COUNT(DISTINCT day) FROM pet_events WHERE kind IN "
                                           "('feed','snack','play','clean','train','pet')").fetchone()[0]
    except sqlite3.OperationalError:
        counts["care_days"] = 0
    xp = sum(WEIGHTS[k] * v for k, v in counts.items())
    stage = max(i for i, (threshold, _, _) in enumerate(STAGES) if xp >= threshold)
    stored = conn.execute("SELECT value FROM app_state WHERE key = ?", (STAGE_KEY,)).fetchone()
    stage = max(stage, int(stored["value"]) if stored and str(stored["value"]).isdigit() else 0)  # só sobe
    nxt = STAGES[stage + 1][0] if stage + 1 < len(STAGES) else None
    base = STAGES[stage][0]
    progress = 1.0 if nxt is None else max(0.0, min(1.0, (xp - base) / (nxt - base)))
    return {"stage": stage, "name": STAGES[stage][1], "description": STAGES[stage][2], "xp": xp, "next_at": nxt,
            "next_name": STAGES[stage + 1][1] if nxt is not None else None, "progress": round(progress, 3),
            "counts": counts, "weights": WEIGHTS,
            "note": "Calculado do que aconteceu de verdade entre vocês; não é sorteado."}


def check_advance(conn: sqlite3.Connection, now: str) -> dict[str, Any] | None:
    """Grava o estágio atual. Devolve a mudança (de, para) quando subiu; na primeira vez só registra."""
    info = compute(conn)
    stored = conn.execute("SELECT value FROM app_state WHERE key = ?", (STAGE_KEY,)).fetchone()
    conn.execute("INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                 (STAGE_KEY, str(info["stage"]), now))
    if stored is None or int(stored["value"]) >= info["stage"]:
        return None
    return {"from": STAGES[int(stored["value"])][1], "to": info["name"], "description": info["description"]}
