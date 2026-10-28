"""Pedidos explícitos do dono no chat (Fase 10): "procure...", "pesquise...", "dá uma olhada em...".

Achado do uso real (19/10): o dono pediu "procure vídeos do canal joguiro" e o Nym respondeu que "já deu uma olhada" e
inventou um vídeo; só quando o dono cobrou ("não minta, realmente procure") ele admitiu. O texto do modelo não é ação.

Aqui o CÓDIGO decide: se a fala do dono é uma ordem clara de buscar/pesquisar algo, isso vira objetivo real na hora
(o mesmo caminho de `commit_from_chat`), e o contexto avisa o Nym que o trabalho começou em segundo plano e que ainda
NÃO há resultado. Detecção por palavras, conservadora: é melhor deixar passar um pedido (o modelo ainda pode usar a
marca `[[fazer: ...]]`) do que criar objetivo de uma frase que não era pedido.
"""
from __future__ import annotations

import re

from app.memory.text import normalize

# Verbos no imperativo (ou "dá uma olhada") que pedem trabalho de busca.
_ASK = re.compile(
    r"\b(procure|procura|pesquise|pesquisa|busque|busca|descubra|encontre|ache|"
    r"de uma (?:olhada|pesquisada|procurada)|da uma (?:olhada|pesquisada|procurada)|"
    r"olha (?:la|ai|se)|veja se|ve se|confira|checa|cheque|"
    r"(?:pode|consegue|poderia|queria que voce|quero que voce|preciso que voce)\s+(?:me\s+)?"
    r"(?:procurar|pesquisar|buscar|achar|encontrar|descobrir))\b"
)
_NEGATED = re.compile(r"\b(nao|nunca|sem)\s+(?:\w+\s+){0,2}$")
_HYPOTHETICAL = re.compile(r"\b(se|quando|caso|talvez|queria saber se)\s+(?:\w+\s+){0,2}$")
_QUESTION_ABOUT_NYM = re.compile(r"\b(voce|vc) (procurou|pesquisou|buscou|achou|encontrou|olhou)\b")
# Formas que também são o presente do indicativo ("você pesquisa"): só valem perto do começo da frase.
_AMBIGUOUS = frozenset({"procura", "pesquisa", "busca", "checa", "ache"})
_SUBJECTS = frozenset({"voce", "vc", "ele", "ela", "eu", "que", "quem", "como", "se", "a", "o", "nym"})
MIN_TOKENS = 3
MAX_DESCRIPTION = 220


def explicit_request(text: str) -> str | None:
    """A frase do pedido (como o dono escreveu, sem mexer), ou None se a fala não é uma ordem de busca."""
    if not text or len(text) > 1200:
        return None
    for sentence in re.split(r"(?<=[.!?\n])\s+", text.strip()):
        wrote = normalize(sentence)
        if _QUESTION_ABOUT_NYM.search(wrote):
            continue
        is_question = sentence.rstrip().endswith("?")
        polite = wrote.startswith(("pode ", "consegue ", "poderia ", "da pra ", "voce pode ", "vc pode ", "d pra "))
        for m in _ASK.finditer(wrote):
            before = wrote[:m.start()]
            lead = before.split()
            # "Pesquisa pra mim qual foi o primeiro vpet?" é ordem mesmo terminando em "?": o verbo abre a frase.
            if is_question and not polite and len(lead) > 2:
                continue
            if lead and lead[-1] in _SUBJECTS:
                continue   # "você pesquisa isso?", "ele busca" descrevem, não ordenam
            if m.group(1) in _AMBIGUOUS and len(lead) > 3:
                continue   # "a forma que você pesquisa..." descreve, não ordena
            if _NEGATED.search(before + " ") or _HYPOTHETICAL.search(before + " "):
                continue
            rest = wrote[m.end():].split()
            if len(rest) < MIN_TOKENS:
                continue
            cleaned = " ".join(sentence.split())
            return cleaned[:MAX_DESCRIPTION]
    return None


def pending_block(description: str, goal_id: int | None = None) -> str:
    """Aviso ao Nym, no contexto da resposta: o pedido virou objetivo, mas nada foi feito ainda."""
    ref = f" (objetivo #{goal_id})" if goal_id else ""
    return ("Pedido dele registrado agora como objetivo seu" + ref + f": \"{description}\". O trabalho começa em segundo "
            "plano e AINDA NÃO TEM RESULTADO: não finja que já olhou, pesquisou ou achou, e não cite vídeo, página ou "
            "fato como se fosse o resultado. Diga, do seu jeito e curto, que vai ver e que conta quando tiver.")


# --- Como anda o pedido? (Fase 10.1) ---------------------------------------------------------------------------------
# Achado da conversa real (21/10): o dono pediu "pesquisa o primeiro vpet" e, 6 segundos depois, perguntou "e aí, já descobriu?".
# O objetivo ainda estava em `needs_plan`, sem resultado, mas o Nym respondeu "Consegui encontrar... Tamagotchi, 1996": resultado
# inventado. O texto do modelo não é ação. Aqui o CÓDIGO diz ao Nym o estado real do último pedido vindo do chat.
_PROGRESS = re.compile(
    r"\b(ja (?:descobriu|achou|encontrou|olhou|viu|pesquisou|terminou|acabou|fez|saiu|tem (?:a )?resposta)|"
    r"(?:descobriu|achou|encontrou|conseguiu|terminou|acabou) (?:alguma coisa|algo|aquilo|a resposta|o que|isso)|"
    r"e ai,? (?:ja|descobriu|achou|conseguiu|saiu)|como (?:esta|ta|anda) (?:aquilo|a pesquisa|o pedido|a busca)|"
    r"deu certo (?:a|aquela)|ficou pronto|ja ficou pronto|saiu (?:o )?resultado|conseguiu (?:achar|ver|fazer|terminar))\b"
)
_PENDING_STATES = ("active", "paused", "proposed")
_STATE_TEXT = {
    "needs_plan": "ainda na fila: o trabalho nem começou",
    "idle": "esperando a vez dele na fila",
    "running": "em andamento agora",
}


def asks_progress(text: str) -> bool:
    """A fala do dono cobra o resultado de um pedido anterior ("e aí, já descobriu?")."""
    return bool(text) and len(text) <= 300 and bool(_PROGRESS.search(normalize(text)))


def latest_chat_goal(conn, now_iso_floor: str) -> dict | None:
    """O objetivo mais recente criado a partir do chat (pedido ou promessa) desde `now_iso_floor`."""
    row = conn.execute(
        "SELECT id, description, status, plan_state, result, last_error, created_at FROM goals "
        "WHERE chat_message_id IS NOT NULL AND mode = 'task' AND created_at >= ? ORDER BY id DESC LIMIT 1",
        (now_iso_floor,)).fetchone()
    return dict(row) if row else None


def status_block(goal: dict | None) -> tuple[str, bool]:
    """(texto para o contexto, o pedido continua SEM resultado?). Texto vazio se não há pedido recente."""
    if not goal:
        return "", False
    head = f"Situação do pedido dele (registro real do objetivo #{goal['id']}: \"{goal['description'][:140]}\")"
    if goal["status"] == "done" and goal.get("result"):
        return (head + f": CONCLUÍDO. Resultado registrado: {goal['result'][:400]}. Conte isso do seu jeito, curto, só com o que está "
                "registrado.", False)
    if goal["status"] == "cancelled":
        return head + ": foi CANCELADO, sem resultado. Diga isso, sem inventar.", True
    if goal.get("last_error"):
        return (head + f": travou, sem resultado. Problema registrado: {goal['last_error'][:200]}. Diga que não conseguiu até agora e "
                "que vai tentar de novo, sem inventar resposta.", True)
    state = _STATE_TEXT.get(goal.get("plan_state") or "", "ainda não terminou")
    return (head + f": {state}. AINDA NÃO TEM RESULTADO. Diga isso do seu jeito, curto (\"ainda não saiu\", \"tá na fila\"), sem "
            "citar fato, vídeo ou página como se fosse o resultado da pesquisa. Se quiser, diga o que você já sabia de cabeça "
            "deixando claro que NÃO é o resultado da busca.", True)


def no_request_block() -> str:
    """Ele cobrou ("e aí, já descobriu?") mas não há NENHUM pedido do chat registrado como objetivo nas últimas 48 h.

    Achado da conversa real (22/10): "Pesquisa pra mim qual foi o primeiro vpet?" não virou objetivo e, na cobrança, o Nym
    respondeu "Já descobri! O Dogz...": resultado de uma pesquisa que nunca existiu. O contexto agora diz a verdade."""
    return ("Situação do pedido dele: NÃO existe nenhum pedido seu registrado como objetivo nos últimos dias (nem andando, nem "
            "concluído). Você não pesquisou nada. Não diga que descobriu, achou ou olhou. Diga, curto e do seu jeito, que não tem "
            "nada rodando sobre isso e pergunte a que ele se refere, ou ofereça ver agora. Se souber algo de cabeça, diga que é "
            "de cabeça e não resultado de busca.")
