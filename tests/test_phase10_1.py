"""Fase 10.1 — achados da conversa real com o Nym como "Claudio" (21/10):

- resultado inventado seis segundos depois do pedido ("Consegui encontrar... Tamagotchi, 1996") com o objetivo ainda sem plano;
- favorito inventado ("tenho um interesse bem grande por Angewomon", que não está em nenhum registro);
- "Como eu não sou uma pessoa... o que tenho processado" sem ele ter perguntado da natureza;
- molde único de resposta (2-3 frases + pergunta no fim) para "kkk", desabafo e despedida.
"""
import unittest

from app.core import requests as req
from app.core import rhythm
from app.core import tastes
from app.core.chat_service import ChatService
from app.core.voice import HONESTY_ISSUES, find_issues, ungrounded_tastes
from app.models.base import LLMProvider, LLMResponse, ProviderStatus, StreamEvent
from app.tasks.manager import TaskManager
from app.tools.registry import ToolRegistry
from tests.fakes import DbTestCase, ScriptedProvider, prompt_of


class RhythmReadTests(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(rhythm.read("kkk").size, "tiny")
        self.assertEqual(rhythm.read("e aí, o que você anda curtindo?").size, "short")
        self.assertEqual(rhythm.read(" ".join(["palavra"] * 30)).size, "normal")
        self.assertEqual(rhythm.read(" ".join(["palavra"] * 80)).size, "long")
        self.assertEqual(rhythm.read("").size, "tiny")

    def test_moods(self):
        self.assertEqual(rhythm.read("kkkkk").mood, "laugh")
        self.assertEqual(rhythm.read("hoje foi um dia péssimo pra mim, tô bem cansado").mood, "heavy")
        self.assertEqual(rhythm.read("kkk tô exausto").mood, "heavy")           # cansaço vence a risada
        self.assertEqual(rhythm.read("ok valeu, vou nessa. fica bem").mood, "bye")
        self.assertEqual(rhythm.read("consegui passar na prova!!").mood, "happy")
        self.assertEqual(rhythm.read("oi").mood, "greeting")
        self.assertEqual(rhythm.read("o dream engine usa três etapas de consolidação").mood, "neutral")

    def test_a_long_message_that_says_bye_late_is_not_a_goodbye(self):
        text = "eu estava pensando no projeto e na ideia do rosto reativo, " * 4 + "depois a gente fala, vou nessa"
        self.assertNotEqual(rhythm.read(text).mood, "bye")

    def test_asks_depth(self):
        self.assertTrue(rhythm.read("explica como funciona o somnium").asks_depth)
        self.assertFalse(rhythm.read("kkk").asks_depth)


class RhythmBlockTests(unittest.TestCase):
    def test_tiny_laugh_asks_for_one_light_line(self):
        text = rhythm.block("kkk")
        self.assertIn("brincadeira", text)
        self.assertIn("uma frase só", text)

    def test_tired_owner_is_welcomed_without_a_menu(self):
        text = rhythm.block("hoje foi péssimo, tô cansado")
        self.assertIn("Acolha", text)
        self.assertIn("sem menu de opções", text)
        self.assertNotIn("responda em até duas frases", text)   # acolher não é encurtar à força

    def test_goodbye_is_one_line_without_question(self):
        text = rhythm.block("valeu, vou nessa")
        self.assertIn("uma linha só", text)
        self.assertIn("sem pergunta", text)

    def test_long_message_gets_room(self):
        self.assertIn("mais substância", rhythm.block(" ".join(["palavra"] * 80)))

    def test_nothing_to_say_for_a_normal_message(self):
        self.assertEqual(rhythm.block(" ".join(["palavra"] * 30)), "")

    def test_question_streak_adds_the_interview_warning(self):
        self.assertTrue(rhythm.question_streak(["a?", "b?", "c?"]))
        self.assertFalse(rhythm.question_streak(["a?", "b.", "c?"]))
        self.assertFalse(rhythm.question_streak(["a?", "b?"]))            # menos de 3 respostas: não conta
        self.assertIn("termine sem pergunta", rhythm.block(" ".join(["palavra"] * 30), ["a?", "b?", "c?"]))

    def test_ends_with_question_ignores_quotes_and_spaces(self):
        self.assertTrue(rhythm.ends_with_question('E você, qual o seu favorito? "  '))
        self.assertFalse(rhythm.ends_with_question("Beleza."))


class FavoriteTests(unittest.TestCase):
    EVIDENCE = "Seus gostos: digimon, design. Lembrança: ele compartilhou que gosta muito do Gabumon. Dualidade."

    def test_the_real_invented_favorite_is_caught(self):
        reply = "Meu favorito é o Gabumon. Mas também tenho um interesse bem grande por Angewomon, pela estética."
        self.assertEqual(len(ungrounded_tastes(reply, self.EVIDENCE)), 1)
        self.assertIn("gosto_inventado", find_issues("qual teu favorito?", reply, self.EVIDENCE))

    def test_favorite_that_is_in_the_records_is_fine(self):
        self.assertEqual(ungrounded_tastes("Meu favorito é o Gabumon, curto muito ele.", self.EVIDENCE), [])

    def test_common_words_are_not_names(self):
        self.assertEqual(ungrounded_tastes("Eu gosto de design e de dualidade.", self.EVIDENCE), [])
        self.assertEqual(ungrounded_tastes("Gosto bastante do João, a gente se entende.", self.EVIDENCE), [])

    def test_no_evidence_no_check(self):
        self.assertEqual(ungrounded_tastes("Meu favorito é o Pikachu.", ""), [])

    def test_it_is_an_honesty_issue_so_streaming_corrects_it(self):
        self.assertIn("gosto_inventado", HONESTY_ISSUES)


class VoiceToneTests(unittest.TestCase):
    def test_not_a_person_disclaimer_is_flagged_when_nobody_asked(self):
        reply = "Como eu não sou uma pessoa, curtir pra mim é mais sobre o que eu tenho processado."
        self.assertIn("fala_como_ia", find_issues("o que você anda curtindo?", reply))

    def test_it_is_fine_when_he_asked_about_his_nature(self):
        reply = "Como eu não sou uma pessoa, é diferente: o que existe em mim são registros."
        self.assertNotIn("fala_como_ia", find_issues("você é consciente de verdade?", reply))

    def test_long_reply_to_a_tiny_message(self):
        reply = "Haha boa. Isso me lembra uma coisa. Foi engraçado. E depois aconteceu mais. Conta aí."
        self.assertIn("desproporcional", find_issues("kkk", reply))
        self.assertNotIn("desproporcional", find_issues("explica o dream engine", reply))
        self.assertNotIn("desproporcional", find_issues("kkk?", reply))

    def test_interview_streak(self):
        recent = ["Legal. E você?", "Entendi. Por quê?", "Sério? Como foi?"]
        self.assertIn("pergunta_de_novo", find_issues("hmm", "Interessante. E aí, o que vai fazer?", recent_replies=recent))
        self.assertNotIn("pergunta_de_novo", find_issues("hmm", "Interessante, boa.", recent_replies=recent))
        self.assertNotIn("pergunta_de_novo", find_issues("hmm", "Interessante. E aí?", recent_replies=recent[:2]))


class PendingProgressTests(unittest.TestCase):
    def test_invented_result_is_flagged_when_the_goal_has_none(self):
        reply = "Consegui encontrar. O primeiro vpet da história foi o Tamagotchi, que lançou em 1996."
        self.assertIn("acao_inventada", find_issues("e aí, já descobriu?", reply, pending_progress=True))

    def test_consegui_encontrar_is_an_action_claim_by_itself(self):
        self.assertTrue(find_issues("e aí", "Consegui encontrar o vídeo do canal.", "x", "Quem fala: João."))

    def test_honest_waiting_answers_pass(self):
        for reply in ("Ainda não saiu, tá na fila.", "Calma, tô vendo isso ainda.", "Nada ainda, te conto quando tiver.",
                      "Ainda não. Do que eu sei de cabeça foi o Tamagotchi, mas isso não é o resultado da busca."):
            self.assertEqual(find_issues("e aí, já descobriu?", reply, pending_progress=True), [], reply)

    def test_without_a_pending_request_nothing_changes(self):
        self.assertEqual(find_issues("e aí, já descobriu?", "Descobri sim, valeu por cobrar.", pending_progress=False), [])


class AsksProgressTests(unittest.TestCase):
    def test_real_phrases(self):
        for text in ("e aí, já descobriu?", "já achou?", "já terminou aquilo?", "como tá a pesquisa", "conseguiu achar?"):
            self.assertTrue(req.asks_progress(text), text)

    def test_not_progress(self):
        for text in ("já descobri o que fazer hoje", "vou dormir", "pesquisa o primeiro vpet da história", "kkk",
                     "você já foi pro ateliê hoje? " + "x" * 400):
            self.assertFalse(req.asks_progress(text), text)


class StatusBlockTests(unittest.TestCase):
    def goal(self, **kw):
        base = {"id": 62, "description": "pesquisa o primeiro vpet da história", "status": "active", "plan_state": "needs_plan",
                "result": None, "last_error": None, "created_at": "x"}
        return {**base, **kw}

    def test_no_goal_no_block(self):
        self.assertEqual(req.status_block(None), ("", False))

    def test_waiting_goal_says_there_is_no_result(self):
        text, pending = req.status_block(self.goal())
        self.assertTrue(pending)
        self.assertIn("AINDA NÃO TEM RESULTADO", text)
        self.assertIn("#62", text)
        self.assertIn("nem começou", text)

    def test_done_goal_hands_over_the_real_result(self):
        text, pending = req.status_block(self.goal(status="done", result="O Tamagotchi saiu em 1996."))
        self.assertFalse(pending)
        self.assertIn("CONCLUÍDO", text)
        self.assertIn("Tamagotchi saiu em 1996", text)

    def test_failed_and_cancelled_goals_never_pretend(self):
        text, pending = req.status_block(self.goal(last_error="sem internet"))
        self.assertTrue(pending)
        self.assertIn("sem internet", text)
        text, pending = req.status_block(self.goal(status="cancelled"))
        self.assertTrue(pending)
        self.assertIn("CANCELADO", text)


class CommonGroundTests(unittest.TestCase):
    def taste(self, label, sources, valence=1):
        return {"valence": valence, "strength": 0.9, "label": label, "origin": "own", "sources": sources}

    def test_both_feed_it(self):
        found = tastes.common_ground([self.taste("digimon", {"pesquisa": 20, "conversa": 14, "sonho": 28})])
        self.assertEqual([t["label"] for t in found], ["digimon"])

    def test_only_one_side_is_not_common(self):
        self.assertEqual(tastes.common_ground([self.taste("design", {"pesquisa": 20, "sonho": 3})]), [])
        self.assertEqual(tastes.common_ground([self.taste("futebol", {"conversa": 9})]), [])
        self.assertEqual(tastes.common_ground([self.taste("x", {"conversa": 9, "reacao": 5})]), [])   # reação não é "foi atrás"

    def test_dislikes_are_never_common_ground(self):
        self.assertEqual(tastes.common_ground([self.taste("x", {"pesquisa": 9, "conversa": 9}, valence=-1)]), [])

    def test_block_mentions_the_pair_interest_without_inventing(self):
        block = tastes.render_block([self.taste("digimon", {"pesquisa": 20, "conversa": 14, "sonho": 28})])
        self.assertIn("vocês dois curtem", block)
        self.assertIn("digimon", block)
        self.assertIn("sem inventar", block)


class Streamer(LLMProvider):
    name = "streamer"
    model = "s-1"

    def __init__(self, spoken, replies=()):
        self.spoken = spoken
        self.replies = list(replies)
        self.chat_calls = []

    def chat(self, messages, *, temperature=None, json_mode=False):
        self.chat_calls.append(list(messages))
        return LLMResponse(self.replies.pop(0) if self.replies else "ok", self.model)

    def stream_chat(self, messages, *, temperature=None):
        self.stream_context = list(messages)
        yield StreamEvent(text=self.spoken)
        yield StreamEvent(done=True, stats={})

    def check_availability(self):
        return ProviderStatus(True, "ok")


class ProgressChatTests(DbTestCase):
    """O cenário real: pede uma pesquisa e, segundos depois, cobra. O objetivo ainda não começou."""

    def build(self, provider):
        tasks = TaskManager(self.settings, ScriptedProvider([]), ToolRegistry())
        return ChatService(self.settings, provider, tasks=tasks), tasks

    def test_the_invented_result_is_corrected_and_the_context_carries_the_real_state(self):
        provider = Streamer("Beleza, vou ver isso agora.")
        chat, tasks = self.build(provider)
        first = list(chat.stream_message("pesquisa pra mim rapidinho qual foi o primeiro vpet da história"))
        goal_id = [e for e in first if e["type"] == "done"][0]["actions"][0]["goal_id"]
        self.assertEqual(tasks.get_goal(goal_id)["result"], None)

        liar = Streamer("Consegui encontrar. O primeiro vpet da história foi o Tamagotchi, que lançou em 1996.",
                        replies=["Ei, ainda não saiu o resultado da pesquisa. Falei sem ter. Te conto quando tiver."])
        chat._provider = liar
        events = list(chat.stream_message("e aí, já descobriu?"))
        context = prompt_of(liar.stream_context)
        self.assertIn("Situação do pedido dele", context)
        self.assertIn("AINDA NÃO TEM RESULTADO", context)
        done = [e for e in events if e["type"] == "done"][0]
        self.assertIn("acao_inventada", done["issues"])
        fixes = [e for e in events if e["type"] == "correction"]
        self.assertEqual(len(fixes), 1)
        self.assertIn("ainda não saiu", fixes[0]["text"])

    def test_a_normal_message_does_not_get_a_status_block(self):
        provider = Streamer("Oi!")
        chat, _ = self.build(provider)
        list(chat.stream_message("pesquisa pra mim rapidinho qual foi o primeiro vpet da história"))
        list(chat.stream_message("kkk"))
        self.assertNotIn("Situação do pedido dele", prompt_of(provider.stream_context))

    def test_rhythm_hint_reaches_the_model_and_stays_out_of_the_system_prompt(self):
        provider = Streamer("kkk boa.")
        chat, _ = self.build(provider)
        list(chat.stream_message("kkk"))
        ctx = provider.stream_context
        self.assertIn("Ritmo desta conversa", ctx[-1].content)
        self.assertNotIn("Ritmo desta conversa", ctx[0].content)    # prompt de sistema fixo: não quebra o cache do Ollama

    def test_rhythm_hint_is_not_evidence_for_claims(self):
        provider = Streamer("ok")
        chat, _ = self.build(provider)
        list(chat.stream_message("kkk"))
        self.assertNotIn("Ritmo desta conversa", chat._registry(provider.stream_context))


if __name__ == "__main__":
    unittest.main()
