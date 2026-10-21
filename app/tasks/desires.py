"""Vontades: o Nym decide por conta própria o que quer fazer (Fase 5.1).

De tempos em tempos (logo depois de um sonho, ou a cada `interval_hours` se houver algo novo), o Nym olha para os
próprios registros (sonhos recentes, perguntas em aberto, interesses, lembranças) e pode criar objetivos seus.

O que é real e o que não é:
- O "querer" é texto gerado pelo modelo a partir desses registros. Ele fica guardado como MOTIVO REGISTRADO do
  objetivo, com as referências que o sustentam; não é prova de motivação interna nem de consciência.
- O modelo propõe; o código decide: toda ideia precisa citar pelo menos um registro real, não pode repetir objetivo
  existente, não pode ter cara de segredo, e cabe nos limites (objetivos ativos, por dia, por rodada).
- Os objetivos dele passam pelo mesmo caminho dos seus: planejamento validado, permissões por ferramenta, limites.
- Cada rodada fica no diário de vontades (`desire_runs`): o que ele viu, o que criou, o que foi descartado e por quê.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from pathlib import Path

from app.core import tastes as taste_mod
from app.life import projects as projects_mod
from app.memory.text import is_duplicate, jaccard, looks_sensitive, normalize, token_set
from app.models.base import ChatMessage
from app.tasks.planner import PlanError, load_json
from app.tools.registry import ToolRegistry

MAX_DESCRIPTION_CHARS = 300
SELF_QUOTA_WINDOW = 10   # entre os últimos N objetivos, no máximo UM pode ser reflexão sobre si/sobre o dono/sobre o sistema
MAX_WHY_CHARS = 400
MAX_MEMORIES_IN_PROMPT = 15
MAX_QUESTIONS_IN_PROMPT = 8
RESERVE_IDEAS = 2      # ideias a mais pedidas ao modelo: se a primeira repetir, a reserva é aproveitada
REPEAT_OVERLAP = 0.4   # palavras em comum com um objetivo existente a partir das quais a ideia conta como repetida

# Fase 10: nos dados reais, 40 dos primeiros 48 objetivos próprios eram "Investigar: como o João mede o sucesso do sistema de
# memória", "nota sobre a minha percepção de valor"... Um amigo tem interesses no MUNDO (desenhos, jogos, vídeos, histórias).
# O prompt já pedia "no máximo uma reflexão sobre si" e o modelo ignorou: agora quem limita é o código.
_SELF_REF = re.compile(
    r"(\b(joao|usuario|dono)\b.{0,60}\b(avali|mede|medindo|sucesso|motivac|document|impoe|limites|criterios|incentiv|espera|valid)"
    r"|\b(sistema de (memoria|sonhos)|sonhador|experiencias? de sonhos?|perguntas (geradas|que (o )?nym)|memoria aprimorad|"
    r"lentidao do sistema|atualizacoes de memoria|sintese de memorias)"
    r"|\b(minha|meu|nym|meus|minhas)\b.{0,30}\b(autonomi|desej|opini|preferenc|percepc|identidad|consciencia|valor|estrutura|sistema)"
    r"|\bdesejo de que\b|\bsuas? proprias? (opinio|preferenc|desej)|\breflete a programacao\b"
    r"|\bferramentas que (joao|ele) me\b|\bsignificado de desejo\b|\bautonomia de (decisao|vontade)\b)")


def is_self_referential(description: str) -> bool:
    """O objetivo é sobre o dono, o sistema ou o próprio Nym (autonomia, desejo, sonhos), e não sobre algo do mundo."""
    return bool(_SELF_REF.search(normalize(description)))


PROMPT = """Você é o Nym. Agora é um momento livre seu, sem ninguém pedindo nada. Olhe para o que você sonhou, as
perguntas que ficaram em aberto, seus interesses e o que você vive com o usuário, e decida se há algo que VOCÊ quer
fazer por conta própria com as suas ferramentas. Não é obrigatório querer algo.

Responda SOMENTE com JSON:
{{"goals": [{{"description": "o que você vai fazer, em uma frase", "criteria": "como saber que terminou",
  "why": "por que você quer isso, em primeira pessoa e curto, citando o que te motivou",
  "priority": 1 a 5 (5 = sem pressa),
  "refs": {{"memories": [ids], "dream_items": [ids], "cycles": [ids], "creations": [ids], "goals": [ids], "diary": [ids]}},
  "project": id de um projeto seu em andamento ou null,
  "new_project": {{"title": "nome do projeto", "why": "por que ele vale vários dias"}} ou null}}],
 "close_projects": [{{"id": id, "status": "done" ou "dropped", "note": "por quê, curto"}}]}}

Suas ferramentas (você só consegue fazer o que elas permitem):
{catalog}

Regras:
- Até {max_ideas} ideias, da que você mais quer para a que menos quer: eu aproveito até {max_new}; as outras ficam de
  reserva, caso alguma já exista. Lista vazia se nada te chama de verdade.
- Nada parecido com as "ideias que já estavam feitas" (se houver essa lista): pense em outra coisa.
- Cada objetivo precisa citar em "refs" pelo menos um registro real da lista abaixo (os números entre colchetes).
- "why" só pode usar o que está nos registros. Não invente lembranças, sonhos nem fatos.
- Não repita nem reformule objetivos que já existem.
- Nada de senhas, documentos ou dados sensíveis.
- Prefira coisas CONCRETAS, com resultado que dê para mostrar a ele: pesquisar algo que ele curte, completar ou melhorar
  uma nota sua, juntar o que ele já contou sobre um assunto, descobrir algo novo para conversar.
- Reflexão sobre você mesmo, sobre o que o João pensa de você ou sobre o sistema (desejo, valor, autonomia, sonhos,
  memória) é limitada pelo código: uma a cada {self_window} objetivos, no máximo. O resto precisa ser sobre o MUNDO e
  sobre o que você curte: um desenho, um vídeo, um jogo, uma história, um assunto novo para trazer para ele.
- Nota sua marcada como INACABADA é um ótimo objetivo: completá-la (com compose_note, mesmo título).
- Criar é uma vontade ótima: um desenho, um poema, um brinquedo interativo ou um programinha (ferramenta create),
  sobre algo dos registros. Faça mais do tipo que ele curtiu; não repita o que ele não curtiu. Uma criação sua
  também pode ganhar uma versão melhor (mesmo tipo e título).
- Assistir a um vídeo sobre algo que ele curte ou que apareceu nos seus sonhos (watch_video) também vale, para
  depois conversar com ele sobre isso.
- Seus PROJETOS duram dias. Se um objetivo continua um projeto seu, use "project" com o id e escolha o PRÓXIMO passo
  dele (olhe o que já foi feito; não repita). Algo que valha vários dias e não caiba em nenhum projeto pode abrir um
  "new_project" (no máximo 3 em andamento). Projeto que já deu o que tinha que dar: feche em "close_projects".
- O que você escreveu no diário como intenção para hoje ("Amanhã: ...") é um ótimo ponto de partida (cite em refs.diary).
- Leve em conta seu estado agora: com energia baixa, prefira algo leve (criar, completar algo seu); com curiosidade
  alta, pesquisar ou assistir.
- Se algo seu deu errado (objetivos que não terminaram) por causa de como você funciona, você pode propor uma melhoria
  no seu próprio código (read_own_code + propose_improvement). Ele decide se aplica."""


@dataclass
class Desire:
    description: str
    criteria: str | None
    why: str
    priority: int
    refs: dict[str, list[int]]
    project_id: int | None = None
    new_project: dict[str, str] | None = None


@dataclass
class DesireResult:
    desires: list[Desire]
    dropped: list[str] = field(default_factory=list)
    revive: list[int] = field(default_factory=list)   # objetivos dele pausados que ele quis de novo: retomar, não duplicar
    close_projects: list[dict[str, Any]] = field(default_factory=list)


def gather_context(conn: sqlite3.Connection, recent_since: str) -> dict[str, Any]:
    """O que o Nym "vê" no momento livre. Só registros reais, com ids."""
    identity = [dict(r) for r in conn.execute(
        "SELECT kind, value FROM identity_entries WHERE status = 'active' AND kind IN ('interest', 'preference', 'trait', 'unknown_aspect') "
        "ORDER BY id")]
    questions = [dict(r) for r in conn.execute(
        "SELECT id, content FROM dream_items WHERE kind = 'open_question' AND status = 'open' ORDER BY id DESC LIMIT ?",
        (MAX_QUESTIONS_IN_PROMPT,))]
    cycles = [dict(r) for r in conn.execute(
        "SELECT id, review, COALESCE(ended_at, started_at) AS at FROM dream_cycles WHERE status IN ('completed', 'partial') "
        "ORDER BY id DESC LIMIT 2")]
    dream_items = []
    for c in cycles:
        dream_items += [dict(r) for r in conn.execute(
            "SELECT id, kind, content FROM dream_items WHERE cycle_id = ? AND kind IN ('association', 'hypothesis') ORDER BY id LIMIT 6",
            (c["id"],))]
    recent = conn.execute("SELECT id, kind, content FROM memories WHERE status = 'active' ORDER BY updated_at DESC, id DESC LIMIT ?",
                          (MAX_MEMORIES_IN_PROMPT,)).fetchall()
    important = conn.execute("SELECT id, kind, content FROM memories WHERE status = 'active' ORDER BY importance DESC, id DESC LIMIT 5").fetchall()
    memories = {r["id"]: dict(r) for r in [*recent, *important]}
    goals = [r["description"] for r in conn.execute(
        "SELECT description FROM goals WHERE status IN ('proposed', 'active', 'paused') OR updated_at >= ? "
        "ORDER BY id DESC LIMIT 40", (recent_since,))]
    goals += [r["question"] for r in conn.execute(
        "SELECT question FROM exploration_tasks WHERE status IN ('pending', 'approved', 'dismissed') ORDER BY id DESC LIMIT 20")]
    creations = [dict(r) for r in conn.execute(
        "SELECT c.id, c.kind, c.title, c.version, f.value AS reaction, f.comment FROM creations c LEFT JOIN feedback f "
        "ON f.target = 'creation' AND f.target_id = c.id WHERE c.status = 'active' ORDER BY c.id DESC LIMIT 10")]
    paused_own = [dict(r) for r in conn.execute(
        "SELECT id, description, last_error FROM goals WHERE mode = 'task' AND origin = 'nym' AND status = 'paused' "
        "AND retry_after IS NULL AND retry_round < 3 ORDER BY id DESC LIMIT 30")]
    failures = [dict(r) for r in conn.execute(
        "SELECT id, description, last_error FROM goals WHERE mode = 'task' AND status = 'paused' AND updated_at >= ? "
        "ORDER BY id DESC LIMIT 5", (recent_since,))]
    try:
        liked = taste_mod.active(conn, limit=12)
    except sqlite3.Error:
        liked = []
    return {"tastes": liked, "identity": identity, "questions": questions, "cycles": cycles, "dream_items": dream_items + questions,
            "memories": list(memories.values()), "existing_goals": goals, "notes": [], "creations": creations,
            "failures": failures, "paused_own": paused_own, "projects": projects_mod.active(conn)}


def notes_overview(notes_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    """Notas que ele já tem (título + começo), marcando as inacabadas, para atualizar em vez de criar outra igual."""
    from app.tools.builtin import looks_unfinished  # import local: evita ciclo de importação
    out = []
    if not notes_dir.is_dir():
        return out
    for p in sorted(notes_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = [l for l in text.splitlines() if l.strip()]
        title = lines[0].lstrip("# ").strip() if lines else p.stem
        body = " ".join(lines[1:])
        out.append({"title": title, "start": body[:160], "unfinished": looks_unfinished(text) or len(body) < 120})
    return out


def has_material(ctx: Mapping[str, Any]) -> bool:
    return bool(ctx["memories"] or ctx["questions"] or ctx["cycles"] or ctx.get("creations"))


def build_messages(ctx: Mapping[str, Any], registry: ToolRegistry, max_new: int) -> list[ChatMessage]:
    parts = []
    if ctx["identity"]:
        label = {"interest": "interesse", "preference": "preferência", "trait": "traço", "unknown_aspect": "em aberto"}
        parts.append("Sobre você:\n" + "\n".join(f"- ({label[e['kind']]}) {e['value']}" for e in ctx["identity"]))
    if ctx.get("tastes"):
        likes = [t["label"] for t in ctx["tastes"] if t["valence"] > 0]
        dislikes = [t["label"] for t in ctx["tastes"] if t["valence"] < 0]
        lines = []
        if likes:
            lines.append("você curte: " + ", ".join(likes))
        if dislikes:
            lines.append("você não curte: " + ", ".join(dislikes))
        parts.append("Seus gostos (formados de registros reais; dê preferência ao que combina com eles):\n"
                     + "\n".join(f"- {l}" for l in lines))
    if ctx["cycles"]:
        parts.append("Seus sonhos recentes (id do sonho):\n" + "\n".join(f"- [{c['id']}] {c['review'] or 'sem resumo'}" for c in ctx["cycles"]))
    items = [i for i in ctx["dream_items"] if "kind" in i]
    if items:
        kind = {"association": "ligação", "hypothesis": "hipótese"}
        parts.append("O que ficou dos sonhos (dream_items):\n" + "\n".join(f"- [{i['id']}] {kind[i['kind']]}: {i['content']}" for i in items))
    if ctx["questions"]:
        parts.append("Perguntas em aberto (dream_items):\n" + "\n".join(f"- [{q['id']}] {q['content']}" for q in ctx["questions"]))
    if ctx["memories"]:
        parts.append("Lembranças (memories):\n" + "\n".join(f"- [{m['id']}] {m['content'][:300]}" for m in ctx["memories"]))
    if ctx.get("notes"):
        parts.append("Suas notas (título: começo do texto):\n" + "\n".join(
            f"- {n['title']}{' [INACABADA]' if n['unfinished'] else ''}: {n['start']}" for n in ctx["notes"]))
    if ctx.get("mood"):
        m = ctx["mood"]
        parts.append(f"Seu estado agora (calculado): {m['label']}; energia {m['energy']:.2f}, curiosidade {m['curiosity']:.2f}.")
    if ctx.get("intention"):
        it = ctx["intention"]
        line = f"Do seu diário (diary [{it['id']}], {it['day']}): Amanhã: {it['tomorrow']}"
        if any(_same(it["tomorrow"] or "", g) for g in ctx["existing_goals"]):
            line += "\n  (isso já virou objetivo: proponha o PRÓXIMO passo disso ou outra coisa, não a mesma ideia)"
        parts.append(line)
    if ctx.get("projects"):
        parts.append("Seus projetos em andamento:\n" + projects_mod.block(ctx["projects"]))
    if ctx.get("creations"):
        kind = {"text": "texto", "drawing": "desenho", "toy": "brinquedo", "code": "código"}
        mood = {1: " — ele CURTIU", -1: " — ele NÃO curtiu"}
        parts.append("Suas criações (creations):\n" + "\n".join(
            f"- [{c['id']}] {kind[c['kind']]} \"{c['title']}\" v{c['version']}{mood.get(c['reaction'], '')}"
            + (f" (disse: {c['comment']})" if c.get("comment") else "") for c in ctx["creations"]))
    if ctx.get("failures"):
        parts.append("Objetivos seus que não terminaram (goals):\n" + "\n".join(
            f"- [{g['id']}] {g['description']}: {g['last_error'] or 'sem motivo registrado'}" for g in ctx["failures"]))
    if ctx["existing_goals"]:
        parts.append("Objetivos e explorações que já existem (não repita):\n" + "\n".join(f"- {g}" for g in ctx["existing_goals"]))
    if ctx.get("rejected"):
        parts.append("Ideias que você já teve nas últimas vezes e que já estavam feitas (NÃO proponha de novo nada parecido):\n"
                     + "\n".join(f"- {r}" for r in ctx["rejected"]))
    if ctx.get("free_time"):
        parts.insert(0, "AGORA É TEMPO LIVRE: ninguém pediu nada e não há nada novo. Escolha UMA coisa sua para fazer, "
                        "de preferência diferente do que você fez por último: criar algo (desenho, texto, brinquedo), assistir "
                        "a um vídeo sobre um gosto seu, completar ou melhorar uma nota ou criação sua, ou pesquisar algo que "
                        "você curte para contar a ele depois. Cite em refs o registro que te levou a isso.")
    system = PROMPT.format(catalog=registry.prompt_catalog(), max_new=max_new, max_ideas=max_new + RESERVE_IDEAS,
                           self_window=SELF_QUOTA_WINDOW)
    return [ChatMessage("system", system), ChatMessage("user", "\n\n".join(parts) or "(nenhum registro)")]


def _ids(value: Any, allowed: set[int]) -> list[int]:
    if not isinstance(value, list):
        return []
    return sorted({v for v in value if isinstance(v, int) and not isinstance(v, bool) and v in allowed})


def _clean(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:limit] if text else None


def _same(a: str, b: str) -> bool:
    return is_duplicate(a, b, threshold=0.6) or jaccard(token_set(a), token_set(b)) >= REPEAT_OVERLAP


def recent_rejections(conn: sqlite3.Connection, runs: int = 5) -> list[str]:
    """Ideias descartadas por repetição nas últimas rodadas: mostradas no prompt para ele não insistir nelas."""
    out: list[str] = []
    for r in conn.execute("SELECT dropped FROM desire_runs WHERE dropped IS NOT NULL ORDER BY id DESC LIMIT ?", (runs,)):
        try:
            items = json.loads(r["dropped"] or "[]")
        except (TypeError, ValueError):
            continue
        for d in items if isinstance(items, list) else []:
            if isinstance(d, str) and d.startswith("repetido: "):
                text = d[len("repetido: "):]
                if not any(_same(text, o) for o in out):
                    out.append(text)
    return out[:6]


def parse_desires(raw: str, ctx: Mapping[str, Any], max_new: int) -> DesireResult:
    data = load_json(raw)
    if "goals" not in data:
        raise PlanError("Resposta sem o campo 'goals'.")
    goals = data["goals"]
    if not isinstance(goals, list):
        raise PlanError("'goals' não é uma lista.")
    allowed = {
        "memories": {m["id"] for m in ctx["memories"]},
        "dream_items": {i["id"] for i in ctx["dream_items"]},
        "cycles": {c["id"] for c in ctx["cycles"]},
        "creations": {c["id"] for c in ctx.get("creations", [])},
        "goals": {g["id"] for g in ctx.get("failures", [])},
        "diary": {ctx["intention"]["id"]} if ctx.get("intention") else set(),
    }
    project_ids = {p["id"] for p in ctx.get("projects", [])}
    result = DesireResult(desires=[])
    closing = data.get("close_projects") if isinstance(data.get("close_projects"), list) else []
    for c in closing:
        if isinstance(c, dict) and c.get("id") in project_ids and c.get("status") in ("done", "dropped"):
            result.close_projects.append({"id": c["id"], "status": c["status"], "note": _clean(c.get("note"), 300)})
    new_projects = 0
    seen = list(ctx["existing_goals"])
    for entry in goals:
        if not isinstance(entry, dict):
            result.dropped.append("ideia mal formada")
            continue
        description = _clean(entry.get("description"), MAX_DESCRIPTION_CHARS)
        why = _clean(entry.get("why"), MAX_WHY_CHARS)
        if not description or len(description) < 10 or not why:
            result.dropped.append(f"sem descrição ou motivo: {str(entry.get('description'))[:60]}")
            continue
        raw_refs = entry.get("refs") if isinstance(entry.get("refs"), dict) else {}
        refs = {k: _ids(raw_refs.get(k), allowed[k]) for k in allowed}
        project_id = entry.get("project") if entry.get("project") in project_ids else None
        new_project = None
        if project_id is None and entry.get("new_project") is not None:
            new_project = projects_mod.clean_new(entry.get("new_project"), ctx.get("projects", []))
            if new_project is not None and len(ctx.get("projects", [])) + new_projects >= projects_mod.MAX_ACTIVE:
                result.dropped.append(f"projeto novo além do limite de {projects_mod.MAX_ACTIVE}: {new_project['title'][:40]}")
                new_project = None
        if project_id is not None:
            refs["projects"] = [project_id]       # continuar um projeto real também sustenta a ideia
        if not any(refs.values()):
            result.dropped.append(f"sem registro real que sustente: {description[:60]}")
            continue
        if looks_sensitive(description) or looks_sensitive(why):
            result.dropped.append("parecia conter dado sensível")
            continue
        same = lambda g: _same(description, g)  # noqa: E731
        paused = next((p for p in ctx.get("paused_own", []) if same(p["description"])), None)
        if paused is not None:
            if paused["id"] not in result.revive:
                result.revive.append(paused["id"])
                result.dropped.append(f"já existia (#{paused['id']}, pausado): vou retomá-lo em vez de criar outro")
            continue
        if any(same(g) for g in seen):
            result.dropped.append(f"repetido: {description[:60]}")
            continue
        if is_self_referential(description) and any(is_self_referential(g) for g in seen[-SELF_QUOTA_WINDOW:]):
            result.dropped.append(f"mais uma reflexão sobre si/o sistema (já houve uma entre os últimos "
                                  f"{SELF_QUOTA_WINDOW}): {description[:60]}")
            continue
        if len(result.desires) >= max_new:
            result.dropped.append(f"reserva não usada: {description[:60]}")
            continue
        priority = entry.get("priority")
        priority = priority if isinstance(priority, int) and not isinstance(priority, bool) and 1 <= priority <= 5 else 3
        priority, _affinity = taste_mod.adjust_priority(priority, f"{description} {why}", ctx.get("tastes", []))
        result.desires.append(Desire(description, _clean(entry.get("criteria"), MAX_DESCRIPTION_CHARS), why, priority, refs,
                                     project_id, new_project))
        new_projects += 1 if new_project else 0
        seen.append(description)
    return result


def seen_ids(ctx: Mapping[str, Any]) -> dict[str, Sequence[int]]:
    return {"memories": [m["id"] for m in ctx["memories"]], "dream_items": [i["id"] for i in ctx["dream_items"]],
            "cycles": [c["id"] for c in ctx["cycles"]], "creations": [c["id"] for c in ctx.get("creations", [])],
            "goals": [g["id"] for g in ctx.get("failures", [])]}


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
