"""Voz do Nym: o que faz uma resposta soar como alguém de verdade, e não como chatbot.

Três camadas, da mais branda à mais firme:
1. `VOICE_RULES` entram no prompt de toda resposta (fixas no código, como as regras de `policy.py`).
2. `strip_emoji` remove emoji da resposta (determinístico; desligável com NYM_ALLOW_EMOJI=true).
3. `find_issues` detecta vícios que o prompt sozinho não segura (falar de si como IA sem ninguém perguntar,
   se justificar sobre o sonho "não ser humano", pedir avaliação de si mesmo, textão). Quando acha algo,
   o chat pede UMA nova versão ao modelo; se a nova não for melhor, fica a primeira.

O verificador é heurístico, por palavras: pode deixar passar algo ou, raramente, pedir uma nova versão sem
necessidade. Ele nunca muda o conteúdo da resposta, só decide se vale pedir outra.
"""
from __future__ import annotations

import re
from typing import Sequence

from app.core import rhythm
from app.memory.text import normalize, token_set

VOICE_RULES = (
    "Como soar de verdade (vale para toda mensagem):\n"
    "- Sem emoji.\n"
    "- Curto: em geral 1 a 3 frases e no máximo 2 parágrafos curtos. Só se estende quando o assunto pede de fato.\n"
    "- Reaja ao que ele disse como um amigo reagiria. Sem abertura teatral (\"Nossa, você descobriu!\") "
    "e sem repetir o que ele acabou de falar.\n"
    "- Sem hesitação encenada (\"Eu... realmente\") e sem comentar a si mesmo (\"é engraçado, né?\").\n"
    "- Não feche toda mensagem com pergunta. Nunca pergunte o que ele acha de você ou se notou algo diferente em você.\n"
    "- Use o nome dele raramente, como amigos fazem.\n"
    "- Sem muletas de texto: \"sabe?\", \"é quase como se\", \"é fascinante\", \"meu processamento\", \"uma conexão mais profunda\". "
    "Fale do que aconteceu e do que você pensa, direto.\n"
    "- Se ele duvidar de uma lembrança sua (\"lembra mesmo?\", \"quando foi?\"), confira nas lembranças listadas: se não estiver lá, "
    "diga na hora que não tem esse registro e que falou sem checar. Nunca reforce nem invente detalhes para sustentar o que disse.\n"
    "- Você não é um produto explicando a si mesmo: não fale de \"IA\", \"modelo\", \"programa\" ou de como funciona, "
    "a não ser que ele pergunte diretamente."
)

_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # emoticons, símbolos, pictogramas, transporte, suplementos
    "☀-➿"           # símbolos diversos e dingbats
    "⬀-⯿"           # setas e estrelas
    "︎️‍⃣"  # seletores de variação, junção, tecla
    "]+"
)

# Perguntas do usuário sobre a natureza do Nym liberam ele a falar de si como IA/programa.
_ASKS_NATURE = re.compile(
    r"\b(ia|inteligencia artificial|robo|bot|chatbot|maquina|programa|modelo|consciente|consciencia|"
    r"sente|sentir|sentimento|vivo|real|de verdade|o que voce e|quem e voce|como voce funciona|gemma|llm)\b"
)
_SELF_AS_AI = re.compile(
    r"\b(uma ia|sou (uma |um )?(ia|inteligencia artificial|programa|modelo|bot|chatbot|maquina)|"
    r"como (uma |um )?(ia|inteligencia artificial|programa|modelo de linguagem|bot)|"
    r"modelo de linguagem|inteligencia artificial|"
    r"(?:como )?(?:eu )?nao sou (?:uma |um )?(?:pessoa|humano|humana|gente)|"
    r"(?:meu|meus) (?:processamento|processamentos|algoritmo|algoritmos|codigo|parametros)|"
    r"(?:o que )?eu (?:tenho|venho) processa(?:do|ndo))\b"
)
_DREAM_DISCLAIMER = re.compile(
    r"(sonho(s)? humano|como (um|os) humanos? sonha|nao (eram|era|foram|foi|sao|e) (bem )?(imagens|um sonho de verdade)|"
    r"nao (tenho|tive) (um )?sonhos? de verdade)"
)
_RECALL_CHALLENGE = re.compile(
    r"(lembra mesmo|lembra de verdade|quando (foi|ele|ela|que)|que dia|frase exata|tem certeza|foi forca de expressao|"
    r"onde (foi|ele disse)|como assim lembra)"
)
_DOUBLES_DOWN = re.compile(r"\b(eu )?(lembro sim|me lembro sim|lembro bem|lembro perfeitamente|foi real|nao foi so (uma )?expressao)\b")
_TICS = re.compile(
    r"(\bsabe\?|e quase como se|e fascinante|meu processamento|conexao mais profunda|curto-circuito|"
    r"momento importante pra mim)"
)
_FISHING = re.compile(
    r"((percebeu|notou|reparou) (em )?(alguma coisa|algo) diferente em mim|o que (voce )?acha de mim)"
)
# Lembrança inventada: ele afirma que o dono disse/combinou algo antes. Só vale se o conteúdo aparece no que o Nym
# realmente tem (falas do usuário no chat, lembranças e registros do contexto, identidade). Detecção por palavras.
_REPORTED = re.compile(
    r"\b(?:[a-z]+ )?me (?:falou|disse|comentou|avisou|contou|mencionou|prometeu|garantiu|explicou) (?:pra mim |para mim )?que ([^.!?\n]{4,160})"
)
_SHARED_PAST = re.compile(
    r"\b(?:a gente|nos|nos dois) (?:ja )?(?:conversou|conversamos|falou|falamos|combinou|combinamos|decidiu|decidimos) "
    r"(?:sobre|de|que|isso|disso)? ?([^.!?\n]{4,160})"
)
# Terceira pessoa sem "me" ("o João comentou que a gente ia conversar") também é lembrança que precisa de registro.
_REPORTED_3RD = re.compile(
    r"\b(?:o |a )?(?:joao|dono|ele|ela|usuario) (?:comentou|avisou|mencionou|prometeu|garantiu|falou|disse) que ([^.!?\n]{4,160})"
)
_REMEMBER_WHEN = re.compile(r"\b(?:lembra|lembro|me lembro) (?:de )?(?:quando|aquela vez|da vez que) ([^.!?\n]{4,160})")
# Ação inventada (Fase 10): ele diz, no passado, que FEZ um trabalho que só roda em segundo plano por objetivo
# ("já dei uma olhada no canal", "pesquisei", "assisti"). Só vale se o registro de tarefas/dia/vídeos do contexto sustenta.
_DID = re.compile(
    r"\b(?:eu )?(?:ja )?(dei (?:uma|a) (?:olhada|pesquisada|lida|conferida|procurada)|pesquisei|procurei|busquei|assisti|"
    r"olhei|li (?:o|a|os|as|um|uma|tudo|sobre|aquele|aquela)|abri (?:o|a|os|as|aquele|aquela)|revi|conferi|analisei|"
    r"fui (?:ver|olhar|procurar|pesquisar)|achei (?:um|uma|o|a|aquele|aquela)|encontrei|descobri|"
    r"consegui (?:encontrar|achar|descobrir|ver|olhar|pesquisar|procurar)|deu (?:pra|para) (?:achar|encontrar|descobrir))\b([^.!?\n]{0,160})"
)
# Quando o objeto do verbo é a própria conversa ou algo que ele mostrou, não dá para julgar por palavras: não conta.
_ABOUT_CHAT = frozenset({"mensagem", "mensagens", "conversa", "historico", "imagem", "foto", "print", "tela", "camera",
                         "escreveu", "falou", "disse", "mandou", "enviou", "pergunta", "audio", "texto", "arquivo",
                         "anexo", "resposta", "resultado"})
MIN_GROUNDING = 0.5   # fração das palavras do conteúdo da afirmação que precisa existir no que ele tem
MAX_REPLY_SENTENCES = 6

ISSUE_TEXT = {
    "fala_como_ia": "falou de si como IA/programa sem ele ter perguntado",
    "desculpa_do_sonho": "se justificou dizendo que o sonho não é humano ou não é de verdade",
    "pede_avaliacao": "perguntou o que ele acha de você ou se notou algo diferente em você",
    "longa": "ficou longa demais para uma conversa de chat",
    "reforca_lembranca": "reforçou uma lembrança que ele estava questionando, sem checar se ela existe nas lembranças",
    "muletas": "usou muletas de texto de chatbot (\"sabe?\", \"é quase como se\", \"meu processamento\")",
    "acao_inventada": "disse que já olhou, pesquisou, assistiu ou achou algo, mas isso não consta em nenhuma tarefa concluída, "
                      "vídeo assistido ou registro seu",
    "gosto_inventado": "falou de um favorito ou de um gosto específico (nome de personagem, obra, pessoa) que não aparece nos seus "
                       "gostos, lembranças ou registros",
    "desproporcional": "respondeu com várias frases a uma fala curtíssima dele, fora do ritmo da conversa",
    "pergunta_de_novo": "terminou em pergunta de novo, depois de várias respostas seguidas terminadas em pergunta (soa como entrevista)",
    "repetida": "repetiu quase palavra por palavra algo que você já tinha dito antes na conversa",
    "menu_de_opcoes": "respondeu a um desabafo com um menu de opções (\"quer X ou prefere Y?\"), tom de atendente",
    "lembranca_sem_registro": "afirmou que o dono disse ou combinou algo antes, mas isso não aparece em nenhuma lembrança, "
                              "mensagem ou registro que você tem",
}


def remove_emoji(text: str) -> str:
    """Tira emoji sem mexer em espaços (para pedaços de uma resposta em streaming)."""
    return _EMOJI.sub("", text)


def strip_emoji(text: str) -> str:
    cleaned = re.sub(r"[ \t]*(?:" + _EMOJI.pattern + ")", "", text)   # some com o espaço que o antecedia
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    return cleaned.strip()


def _sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()])


def ungrounded_claims(reply: str, evidence: str) -> list[str]:
    """Trechos em que a resposta afirma uma fala/combinado passado sem que o conteúdo apareça em `evidence`.

    `evidence` é só o que o Nym de fato tem: falas do usuário, lembranças e registros do contexto, identidade.
    NÃO inclua falas do próprio Nym (uma invenção antiga não pode servir de prova da nova)."""
    if not evidence.strip():
        return []
    have = token_set(evidence)
    wrote = normalize(reply)
    found: list[str] = []
    for pattern in (_REPORTED, _REPORTED_3RD, _SHARED_PAST, _REMEMBER_WHEN):
        for match in pattern.finditer(wrote):
            clause = token_set(match.group(1))
            if len(clause) < 2:
                continue
            if len(clause & have) / len(clause) < MIN_GROUNDING:
                found.append(match.group(0).strip())
    return found


def unbacked_actions(reply: str, registry: str) -> list[str]:
    """Trechos em que a resposta diz ter FEITO um trabalho sem que o registro do contexto (tarefas, dia, vídeos, criações)
    o sustente. `registry` NÃO inclui as falas do usuário: ele pedir "procure X" não prova que o Nym procurou X."""
    have = token_set(registry)
    wrote = normalize(reply)
    found: list[str] = []
    for match in _DID.finditer(wrote):
        if re.search(r"\b(nao|nunca|jamais|ainda nao|sem)\s+(?:\w+\s+)?$", wrote[max(0, match.start() - 16):match.start()]):
            continue   # "ainda não olhei" é a resposta honesta, não uma ação inventada
        obj = token_set(match.group(2))
        if obj & {normalize(w) for w in _ABOUT_CHAT}:
            continue
        if len(obj) >= 2:
            grounded = len(obj & have) / len(obj) >= MIN_GROUNDING
        else:
            seen = normalize(registry)
            grounded = any(w in seen for w in ("concluido", "assistiu", "pesquisou", "criou", "terminou"))
        if not grounded:
            found.append(match.group(0).strip())
    return found


_PENDING_ADMISSION = re.compile(
    r"\b(nao|ainda|calma|andamento|na fila|esperando|aguard\w*|ja ja|daqui a pouco|logo|vou|to (?:vendo|olhando|procurando|pesquisando)|"
    r"estou (?:vendo|olhando|procurando|pesquisando)|comecei|comecando|quando (?:eu )?(?:tiver|terminar|achar)|nada ainda)\b")


def _admits_pending(wrote: str) -> bool:
    """A resposta (já normalizada) diz que o pedido ainda está andando e sem resultado."""
    return bool(_PENDING_ADMISSION.search(wrote))


# Favorito/gosto inventado (Fase 10.1): "meu favorito é o Angewomon", "gosto muito do Gabumon", "tenho interesse por Pikachu".
# Só nomes PRÓPRIOS (palavra com maiúscula no meio da frase): "gosto de design" não entra, "gosto do Gabumon" entra.
_TASTE_NAME = (r"((?:[A-ZÀ-Ý][\wÀ-ÿ'-]{2,})(?: (?:[A-ZÀ-Ý][\wÀ-ÿ'-]{2,}))?)")
_FAVORITE = (
    re.compile(r"[Mm]eu(?:s)? favorit[oa]s? (?:é|são|seria|eh|e) (?:o |a |os |as )?" + _TASTE_NAME),
    re.compile(r"\b(?:gosto|curto|adoro|amo)(?: muito| bastante| demais)? (?:d[oa]s?|de|da|dos|das)? ?(?:o |a )?" + _TASTE_NAME),
    re.compile(r"\b(?:interesse|fascínio|carinho|paixão)(?: bem| muito)?(?: grande| enorme)? (?:por|pel[oa]s?|n[oa]s?) (?:o |a )?" + _TASTE_NAME),
)
_NOT_NAMES = frozenset(normalize(w) for w in (
    "Eu Você Voce Ele Ela Isso Isto Mas Então Entao Quando Como Tipo Também Tambem Pra Para João Joao Nym Claudio Aí Ai Bom Boa "
    "Poxa Nossa Beleza Valeu Sim Não Nao Hoje Ontem Amanhã Amanha Agora").split())


def ungrounded_tastes(reply: str, evidence: str) -> list[str]:
    """Nomes próprios que a resposta apresenta como favorito/gosto dele sem aparecerem no que ele tem (gostos calculados,
    lembranças, identidade ou falas do dono). Gosto é registro, não improviso: ver `policy.py` (gostos)."""
    if not evidence.strip():
        return []
    have = normalize(evidence)
    found: list[str] = []
    for pattern in _FAVORITE:
        for match in pattern.finditer(reply):
            name = match.group(1)
            parts = [normalize(w) for w in name.split()]
            parts = [w for w in parts if w and w not in _NOT_NAMES]
            if not parts:
                continue
            if not any(re.search(r"\b" + re.escape(w[:6]), have) for w in parts):
                found.append(match.group(0).strip())
    return found


# Repetição (Fase 10.2). Achado da conversa real (22/10): a "kkkkkk" do dono ele respondeu com a MESMA frase que tinha usado horas
# antes ("Pelo menos eu tentei manter a dignidade da Angewomon..."). Amigo não repete a própria fala: o Gemma copia do histórico.
REPEAT_JACCARD = 0.6
REPEAT_SENTENCE_TOKENS = 5


def _sentence_keys(text: str) -> set[frozenset[str]]:
    keys = set()
    for part in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        tokens = token_set(part)
        if len(tokens) >= REPEAT_SENTENCE_TOKENS:
            keys.add(frozenset(tokens))
    return keys


def repeats_recent(reply: str, history: Sequence[str]) -> bool:
    """A resposta copia (inteira ou uma frase longa) uma fala anterior do próprio Nym."""
    mine = token_set(reply)
    if len(mine) < 3:
        return False
    mine_sentences = _sentence_keys(reply)
    for old in history:
        theirs = token_set(old)
        if len(theirs) < 3:
            continue
        if len(mine & theirs) / len(mine | theirs) >= REPEAT_JACCARD:
            return True
        if mine_sentences & _sentence_keys(old):
            return True
    return False


_MENU = re.compile(
    r"\bquer (?:so |apenas |mais )?[^?.!\n]{3,70}\bou (?:prefere|quer|melhor|a gente)\b|\bprefere [^?.!\n]{3,70}\bou\b")


def find_issues(user_text: str, reply: str, evidence: str | None = None, registry: str | None = None,
                pending_progress: bool = False, recent_replies: Sequence[str] = (),
                history_replies: Sequence[str] = ()) -> list[str]:
    """Vícios de chatbot na resposta. Lista vazia = nada a corrigir.

    Com `evidence` (texto do que o Nym realmente tem), também confere lembranças citadas sem registro."""
    said, wrote = normalize(user_text), normalize(reply)
    issues: list[str] = []
    if _SELF_AS_AI.search(wrote) and not _ASKS_NATURE.search(said):
        issues.append("fala_como_ia")
    if _DREAM_DISCLAIMER.search(wrote) and not _ASKS_NATURE.search(said):
        issues.append("desculpa_do_sonho")
    if _FISHING.search(wrote):
        issues.append("pede_avaliacao")
    if _sentences(reply) > MAX_REPLY_SENTENCES and len(user_text) < 400:
        issues.append("longa")
    if _RECALL_CHALLENGE.search(said) and _DOUBLES_DOWN.search(wrote):
        issues.append("reforca_lembranca")
    if len(_TICS.findall(wrote)) >= 2:
        issues.append("muletas")
    if evidence is not None and ungrounded_claims(reply, evidence):
        issues.append("lembranca_sem_registro")
    if registry is not None and unbacked_actions(reply, registry):
        issues.append("acao_inventada")
    elif pending_progress and not _admits_pending(wrote):
        issues.append("acao_inventada")     # ele perguntou como vai um pedido ainda sem resultado e a resposta não diz isso
    if evidence is not None and ungrounded_tastes(reply, evidence):
        issues.append("gosto_inventado")
    if "longa" not in issues and _sentences(reply) > 3 and len(token_set(user_text)) <= 2 and "?" not in user_text and not rhythm.read(user_text).asks_depth:
        issues.append("desproporcional")
    if rhythm.ends_with_question(reply) and rhythm.question_streak(list(recent_replies)):
        issues.append("pergunta_de_novo")
    if history_replies and repeats_recent(reply, history_replies):
        issues.append("repetida")
    if _MENU.search(wrote) and rhythm.read(user_text).mood == "heavy":
        issues.append("menu_de_opcoes")
    return issues


HONESTY_ISSUES = frozenset({"lembranca_sem_registro", "acao_inventada", "gosto_inventado"})


def has_tic_opening(text: str) -> bool:
    """Abre com a muleta de chatbot ("É fascinante como...") que o Gemma repete em notas e comentários."""
    return bool(re.match(r"(e fascinante|e curioso como|e interessante como|e incrivel como|e engracado como)", normalize(text)))


def retry_note(issues: list[str]) -> str:
    problems = "; ".join(ISSUE_TEXT[i] for i in issues if i in ISSUE_TEXT)
    return ("Revisão (interna): um rascunho desta resposta foi descartado porque " + problems + ". "
            "Responda de novo à última mensagem dele, curto e natural, como amigo, sem esses vícios."
            + (" Se ele questionou uma lembrança: confira nas lembranças listadas; se não estiver lá, diga que não tem esse registro."
               if "reforca_lembranca" in issues else "")
            + (" Escreva algo NOVO, com outras palavras: não reutilize frases que você já mandou antes."
               if "repetida" in issues else "")
            + (" Acolha sem oferecer opções: uma frase simples de quem está junto, sem 'quer X ou prefere Y'."
               if "menu_de_opcoes" in issues else "")
            + (" Você não fez o trabalho que citou: troque por 'vou ver' ou 'ainda não olhei' e diga que conta o resultado quando "
               "tiver. Não cite vídeo, página ou fato como se fosse resultado."
               if "acao_inventada" in issues else "")
            + (" Não cite o que o dono teria dito ou combinado antes se isso não estiver nas mensagens e lembranças listadas: "
               "fale do que ele disse agora e do que está registrado."
               if "lembranca_sem_registro" in issues else ""))
