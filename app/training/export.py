"""Exporta exemplos de treino de um LoRA do Nym a partir dos registros REAIS (só biblioteca padrão).

Uso (PowerShell, na pasta do projeto):
    .venv\\Scripts\\python.exe -m app.training.export

Gera em data/training/ (fora do Git, como todo data/):
- nym_train.jsonl e nym_eval.jsonl: um exemplo por linha, no formato {"messages": [...], "meta": {...}};
- relatorio.txt: quantos exemplos, quantos ficaram de fora e por quê.

O que entra:
- Conversa: cada resposta do Nym no chat vira um exemplo, com até CONTEXT_TURNS falas anteriores de contexto.
  Ficam de fora: respostas com vício de voz que não foi corrigido (auditoria do chat), respostas que você marcou
  com 👎, respostas com cara de dado sensível e respostas longas demais. Respostas com 👍 entram em dobro.
  Ou seja: reagir às mensagens no chat é como você escolhe o que o Nym deve aprender.
- Diário: cada página vira um exemplo com o prompt real do diário e os fatos guardados junto da página.

- Planos: a primeira rodada de objetivos concluídos (JSON real que deu certo), para o LoRA não estragar o formato.
  O 1º LoRA (19/10) treinou só conversa e passou a errar o JSON do plano.

O que NÃO entra: conversas de teste feitas por outra IA ("aqui é o Claudio/Claude": o 1º LoRA aprendeu esse nome),
respostas que afirmam "ele me disse..." sem base nas suas falas, iniciativas soltas (sem fala sua antes) e qualquer coisa fora do banco. Os dados nunca saem do computador.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from app.config import Settings
from app.database.connection import connect
from app.life import day as day_mod
from app.life import diary as diary_mod
from app.core.voice import ungrounded_claims
from app.memory.text import looks_sensitive
from app.tasks import planner
from app.tools.registry import ToolRegistry

CONTEXT_TURNS = 6          # falas anteriores mostradas antes da resposta
MAX_TURN_CHARS = 1200      # fala de contexto mais longa que isso é cortada
MAX_REPLY_CHARS = 1500     # resposta mais longa que isso não vira exemplo (não é o jeito dele no chat)
EVAL_FRACTION = 0.08
MAX_PLAN_EXAMPLES = 40     # planos reais que deram certo: mantêm o JSON afiado (o 1º LoRA quebrou o JSON do plano)
# Conversas de teste feitas por outra IA ("aqui é o Claudio/Claude") ensinaram nome e lembranças erradas no 1º LoRA.
TESTER = re.compile(r"\bclaud(?:e|io)\b", re.I)
SEED = 3407

# Prompt de sistema CURTO usado no treino. No uso real o Nym recebe a identidade completa; aqui o objetivo é ensinar
# o jeito de falar, sem gastar a memória da placa com milhares de tokens repetidos em todo exemplo.
TRAIN_SYSTEM = ("Você é o Nym, um companheiro digital que vive no computador do João. Fale como amigo, em português do "
                "Brasil, com mensagens curtas e naturais. Nunca invente lembranças: só cite o que de fato aconteceu.")


def _flagged_replies(conn: sqlite3.Connection) -> set[int]:
    """Respostas cuja auditoria registrou vício de voz que ficou sem correção."""
    out: set[int] = set()
    for r in conn.execute("SELECT detail FROM audit_log WHERE event = 'chat.reply'"):
        try:
            d = json.loads(r["detail"] or "{}")
        except ValueError:
            continue
        voice = d.get("voice") or {}
        issues = voice.get("issues_after") if voice.get("retried") else voice.get("issues")
        if issues and isinstance(d.get("assistant_message_id"), int):
            out.add(d["assistant_message_id"])
    return out


def _reactions(conn: sqlite3.Connection) -> dict[int, int]:
    return {r["target_id"]: r["value"] for r in conn.execute(
        "SELECT target_id, value FROM feedback WHERE target = 'message' ORDER BY id")}


def _turns(rows: Iterable[sqlite3.Row]) -> list[dict[str, str]]:
    """Falas em ordem, juntando falas seguidas do mesmo lado (o formato do Gemma exige alternar)."""
    turns: list[dict[str, str]] = []
    for r in rows:
        role = "user" if r["role"] == "user" else "assistant"
        text = (r["content"] or "").strip()[:MAX_TURN_CHARS]
        if not text:
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + text
        else:
            turns.append({"role": role, "content": text})
    while turns and turns[0]["role"] != "user":   # o contexto começa numa fala sua
        turns.pop(0)
    return turns


def chat_examples(conn: sqlite3.Connection, skipped: Counter) -> list[dict[str, Any]]:
    flagged, reactions = _flagged_replies(conn), _reactions(conn)
    out = []
    replies = conn.execute("SELECT id, conversation_id, content FROM messages WHERE role = 'assistant' "
                           "AND COALESCE(initiative, 0) = 0 ORDER BY id").fetchall()
    for rep in replies:
        text = (rep["content"] or "").strip()
        if rep["id"] in flagged:
            skipped["vício de voz sem correção"] += 1
            continue
        if reactions.get(rep["id"]) == -1:
            skipped["você marcou 👎"] += 1
            continue
        if not text or len(text) > MAX_REPLY_CHARS:
            skipped["vazia ou longa demais"] += 1
            continue
        if looks_sensitive(text):
            skipped["parecia ter dado sensível"] += 1
            continue
        before = conn.execute("SELECT role, content FROM messages WHERE conversation_id = ? AND id < ? AND role != 'system' "
                              "ORDER BY id DESC LIMIT ?", (rep["conversation_id"], rep["id"], CONTEXT_TURNS)).fetchall()
        turns = _turns(reversed(before))
        if not turns or turns[-1]["role"] != "user":
            skipped["sem fala sua antes"] += 1
            continue
        if any(looks_sensitive(t["content"]) for t in turns):
            skipped["contexto com dado sensível"] += 1
            continue
        if TESTER.search(text) or any(TESTER.search(t["content"]) for t in turns):
            skipped["conversa de teste (outra IA)"] += 1
            continue
        said = "\n".join(t["content"] for t in turns if t["role"] == "user")
        if ungrounded_claims(text, said):
            skipped["lembrança sem base no que você disse"] += 1
            continue
        example = {"messages": [{"role": "system", "content": TRAIN_SYSTEM}, *turns, {"role": "assistant", "content": text}],
                   "meta": {"source": "chat", "message_id": rep["id"], "liked": reactions.get(rep["id"]) == 1}}
        out.append(example)
        if reactions.get(rep["id"]) == 1:
            out.append(example)    # o que você curtiu pesa em dobro
    return out


def diary_examples(conn: sqlite3.Connection, skipped: Counter) -> list[dict[str, Any]]:
    out = []
    for p in conn.execute("SELECT id, day, text, tomorrow, facts FROM diary ORDER BY day"):
        try:
            facts = json.loads(p["facts"] or "{}")
            facts_text = day_mod.render(facts)
        except (ValueError, KeyError, TypeError):
            skipped["diário com fatos ilegíveis"] += 1
            continue
        if looks_sensitive(p["text"]):
            skipped["parecia ter dado sensível"] += 1
            continue
        if TESTER.search(p["text"]) or TESTER.search(facts_text):
            skipped["diário citando a conversa de teste"] += 1
            continue
        system, user = diary_mod.build_messages(date.fromisoformat(p["day"]), facts_text)
        answer = p["text"] + (f"\nAmanhã: {p['tomorrow']}" if p["tomorrow"] else "")
        out.append({"messages": [{"role": "system", "content": system.content}, {"role": "user", "content": user.content},
                                 {"role": "assistant", "content": answer}],
                    "meta": {"source": "diary", "diary_id": p["id"]}})
    return out


def plan_examples(conn: sqlite3.Connection, skipped: Counter) -> list[dict[str, Any]]:
    """A primeira rodada de planos que deram certo: objetivo concluído e todos os passos daquela rodada feitos."""
    registry, out = ToolRegistry(), []
    goals = conn.execute("SELECT id, description, completion_criteria FROM goals WHERE mode = 'task' AND status = 'done' "
                         "ORDER BY id DESC").fetchall()
    for g in goals:
        first = conn.execute("SELECT MIN(created_at) FROM tasks WHERE goal_id = ?", (g["id"],)).fetchone()[0]
        if first is None:
            continue
        rows = conn.execute("SELECT title, tool, args, status FROM tasks WHERE goal_id = ? AND created_at = ? ORDER BY step, id",
                            (g["id"], first)).fetchall()
        if not rows or any(r["status"] != "done" for r in rows) or len(rows) > 5:
            skipped["plano que não deu certo de primeira"] += 1
            continue
        try:
            steps = [{"title": r["title"], "tool": r["tool"], "args": json.loads(r["args"] or "{}")} for r in rows]
        except ValueError:
            continue
        if any(s["tool"] not in registry.names() for s in steps):
            skipped["plano com ferramenta que não existe mais"] += 1
            continue
        msgs = planner.plan_messages(dict(g), registry, 5)
        out.append({"messages": [{"role": "system", "content": msgs[0].content}, {"role": "user", "content": msgs[1].content},
                                 {"role": "assistant", "content": json.dumps({"steps": steps}, ensure_ascii=False)}],
                    "meta": {"source": "plan", "goal_id": g["id"]}})
        if len(out) >= MAX_PLAN_EXAMPLES:
            break
    return out


SYNTHETIC = Path(__file__).resolve().parents[2] / "training" / "sintetico.jsonl"


def synthetic_examples(path: Path, skipped: Counter) -> list[dict[str, Any]]:
    """Exemplos escritos à mão (training/sintetico.jsonl): mostram o jeito desejado SEM entrar no banco do Nym,
    então nunca viram lembrança. Passam pelos mesmos filtros; formato inválido é ignorado."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
            msgs = e["messages"]
            ok = (isinstance(msgs, list) and len(msgs) >= 3 and msgs[0]["role"] == "system" and msgs[-1]["role"] == "assistant"
                  and all(isinstance(m.get("content"), str) and m["content"].strip() for m in msgs))
        except (ValueError, KeyError, TypeError, AttributeError):
            ok = False
        if not ok or any(looks_sensitive(m["content"]) for m in msgs):
            skipped["exemplo escrito inválido"] += 1
            continue
        out.append({"messages": msgs, "meta": {**e.get("meta", {}), "source": "sintetico"}})
    return out


def build(conn: sqlite3.Connection, synthetic: Path | None = SYNTHETIC) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter]:
    skipped: Counter = Counter()
    examples = chat_examples(conn, skipped) + diary_examples(conn, skipped) + plan_examples(conn, skipped)
    rng = random.Random(SEED)
    unique_ids = sorted({json.dumps(e["meta"], sort_keys=True) for e in examples})
    rng.shuffle(unique_ids)
    n_eval = max(1, round(len(unique_ids) * EVAL_FRACTION)) if len(unique_ids) >= 20 else 0
    eval_keys = set(unique_ids[:n_eval])           # um mesmo exemplo (curtido em dobro) nunca cai nos dois lados
    train = [e for e in examples if json.dumps(e["meta"], sort_keys=True) not in eval_keys]
    evals = [e for e in examples if json.dumps(e["meta"], sort_keys=True) in eval_keys]
    train += synthetic_examples(synthetic, skipped) if synthetic else []   # escritos à mão: só treino, nunca avaliação
    rng.shuffle(train)
    return train, evals, skipped


def write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in items:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def report(train: list[dict[str, Any]], evals: list[dict[str, Any]], skipped: Counter) -> str:
    by = Counter(e["meta"]["source"] for e in train + evals)
    liked = sum(1 for e in train + evals if e["meta"].get("liked"))
    lines = [f"Exemplos de treino: {len(train)}  |  de avaliação: {len(evals)}",
             f"De onde vieram: {by.get('chat', 0)} da conversa ({liked} contando as curtidas em dobro), {by.get('diary', 0)} do diário, "
             f"{by.get('plan', 0)} planos que deram certo, {by.get('sintetico', 0)} exemplos escritos à mão (training/sintetico.jsonl)"]
    if skipped:
        lines.append("Ficaram de fora: " + "; ".join(f"{k}: {v}" for k, v in skipped.most_common()))
    total = sum(1 for e in train + evals if e["meta"]["source"] != "sintetico")
    if total < 150:
        lines.append(f"Atenção: {total} exemplos é pouco. Um LoRA com isso aprende mais o tom do que coisas novas. "
                     "Reaja às respostas no chat (👍/👎) por alguns dias e exporte de novo.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exporta exemplos reais para treinar um LoRA do Nym.")
    parser.add_argument("--out", default=None, help="pasta de saída (padrão: data/training)")
    parser.add_argument("--sem-sintetico", action="store_true", help="não incluir training/sintetico.jsonl")
    args = parser.parse_args(argv)
    settings = Settings.load()
    out = Path(args.out) if args.out else Path(settings.data_dir) / "training"
    conn = connect(settings.db_path)
    try:
        train, evals, skipped = build(conn, None if args.sem_sintetico else SYNTHETIC)
    finally:
        conn.close()
    write_jsonl(out / "nym_train.jsonl", train)
    write_jsonl(out / "nym_eval.jsonl", evals)
    text = report(train, evals, skipped)
    (out / "relatorio.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"Arquivos em {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
