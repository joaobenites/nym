"""Ritmo da conversa (Fase 10.1): responder no tamanho e no clima da fala do dono, como um amigo faz.

Achado da conversa real com o Nym (21/10, como "Claudio"): quase toda resposta tinha o mesmo molde, duas ou três frases e uma
pergunta no fim, não importava se a fala era "kkk", um desabafo ou uma despedida. Amigo espelha: a um "kkk" responde
com uma linha; a um dia ruim, acolhe antes de perguntar; a um "vou nessa", se despede curto.

Aqui o CÓDIGO lê a fala (tamanho, risada, cansaço, despedida, empolgação) e escreve uma instrução curta que vai no bloco de contexto
da última fala (nunca no prompt de sistema). Detecção por palavras, sem modelo, e só para orientar o TOM: não decide nenhum fato.
A leitura de humor é heurística e pode errar; por isso o texto sempre diz "parece".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.memory.text import normalize

TINY_WORDS = 3
SHORT_WORDS = 12
LONG_WORDS = 60
QUESTION_STREAK = 3          # tantas respostas seguidas terminando em pergunta pedem uma que não termine

_LAUGH = re.compile(r"\b(k{3,}|(?:ha){2,}h?|(?:he){2,}|(?:rs){2,}|rsrs+|lol|lmao)\b")
_HEAVY = re.compile(
    r"\b(cansad[oa]|exaust[oa]|esgotad[oa]|estressad[oa]|ansios[oa]|desanimad[oa]|triste|chatead[oa]|pessimo|pessima|"
    r"horrivel|sem energia|nao aguento|to mal|estou mal|me sinto mal|sozinho|sozinha|chorei|chorando|saudade|"
    r"perdi|morreu|doente|passando mal|dia ruim|dia pesado|dia puxado)\b"
)
_HAPPY = re.compile(
    r"\b(consegui|passei|finalmente|olha isso|olha so|adivinha|boa noticia|fui aprovad[oa]|deu certo|ganhei|"
    r"to feliz|estou feliz|que dia bom|melhor dia)\b"
)
_BYE = re.compile(r"\b(vou nessa|tchau|ate (?:mais|logo|amanha|depois)|vou dormir|vou sair|vou indo|"
                  r"fica bem|ate a proxima|flw|valeu,? (?:ate|tchau))\b")
_GREETING = re.compile(r"^(oi+|ola|e ai|eae|fala|opa|bom dia|boa tarde|boa noite|salve|hey|hello)\b")
_ASKS_DEPTH = re.compile(r"\b(explica|explique|me conta|conta (?:pra|para) mim|como funciona|por que|porque|detalha|resume|"
                         r"qual a diferenca|me ajuda|ajuda)\b")


@dataclass(frozen=True)
class Rhythm:
    size: str              # tiny | short | normal | long
    mood: str              # laugh | heavy | happy | bye | greeting | neutral
    asks_depth: bool
    words: int


def _words(text: str) -> int:
    return len(re.findall(r"[\wÀ-ÿ']+", text))


def read(text: str) -> Rhythm:
    """Lê a fala do dono. Vazia ou sem palavras = pequena e neutra."""
    wrote = normalize(text or "")
    words = _words(text or "")
    size = "tiny" if words <= TINY_WORDS else "short" if words <= SHORT_WORDS else "long" if words > LONG_WORDS else "normal"
    if _HEAVY.search(wrote):
        mood = "heavy"          # cansaço vence risada: "kkk tô exausto" ainda pede acolhimento
    elif _BYE.search(wrote) and words <= 12:
        mood = "bye"
    elif _LAUGH.search(wrote):
        mood = "laugh"
    elif _HAPPY.search(wrote) or (text or "").count("!") >= 2:
        mood = "happy"
    elif _GREETING.search(wrote) and words <= 6:
        mood = "greeting"
    else:
        mood = "neutral"
    return Rhythm(size=size, mood=mood, asks_depth=bool(_ASKS_DEPTH.search(wrote)), words=words)


def ends_with_question(text: str) -> bool:
    return re.sub(r"[\s\"'“”)\]]+$", "", text or "").endswith("?")


def question_streak(replies: list[str], n: int = QUESTION_STREAK) -> bool:
    """As últimas `n` respostas dele terminaram, todas, em pergunta (entrevista: amigo não fecha toda fala assim)."""
    tail = [r for r in replies if r and r.strip()][-n:]
    return len(tail) == n and all(ends_with_question(r) for r in tail)


def block(text: str, recent_replies: list[str] | None = None) -> str:
    """Instrução de ritmo para a próxima resposta (vai no bloco de contexto da última fala)."""
    r = read(text)
    lines: list[str] = []
    if r.mood == "heavy":
        lines.append("Ele parece cansado ou pra baixo. Acolha primeiro, em uma ou duas frases simples, do jeito de amigo: sem piada, "
                     "sem lista de conselhos, sem menu de opções (\"quer desabafar ou prefere mudar de assunto?\"). Uma pergunta aberta e curta é "
                     "ok, ou nenhuma: só estar ali também conta.")
    elif r.mood == "bye":
        lines.append("Ele está se despedindo. Responda com uma linha só, no mesmo clima, sem recapitular a conversa e sem pergunta.")
    elif r.mood == "laugh":
        lines.append("Ele está rindo ou brincando. Entre na brincadeira com uma linha leve; não explique a graça.")
    elif r.mood == "happy":
        lines.append("Ele parece animado com algo. Comemore junto, curto e específico no que ele contou, antes de qualquer pergunta.")
    elif r.mood == "greeting":
        lines.append("É um cumprimento. Responda no mesmo tamanho, leve, como quem reencontra um amigo.")
    if r.size == "tiny" and r.mood not in ("heavy",):
        lines.append("A fala dele é curtíssima: responda com uma frase só (no máximo duas), no mesmo ritmo.")
    elif r.size == "short" and not r.asks_depth and r.mood not in ("heavy",):
        lines.append("A fala dele é curta: responda em até duas frases.")
    elif r.size == "long":
        lines.append("Ele escreveu bastante: responda com mais substância (até uns cinco frases), tratando os pontos que ele levantou, sem repetir o que ele disse.")
    if recent_replies and question_streak(recent_replies):
        lines.append("Suas últimas respostas terminaram todas em pergunta, o que soa como entrevista. Nesta, termine sem pergunta.")
    if not lines:
        return ""
    return "Ritmo desta conversa (leitura automática da fala dele; é só tom, pode estar errada): " + " ".join(lines)
