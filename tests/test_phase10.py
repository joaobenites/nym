"""Fase 10 — achados do uso real de 18-19/10: ação inventada ("já dei uma olhada" sem ter olhado), pedido explícito que
não virava trabalho, visita confundida com o dono e "o que você fez hoje" respondido de cabeça."""
import json
import unittest
from datetime import datetime, timedelta, timezone

from app.core import requests as req
from app.core import speaker
from app.core.chat_service import ChatService, asks_about_today
from app.core.voice import find_issues, retry_note, unbacked_actions
from app.database import repository as repo
from app.database.connection import connect, transaction
from app.models.base import LLMProvider, LLMResponse, ProviderStatus, StreamEvent
from app.tasks.manager import TaskManager
from app.tools.registry import ToolRegistry
from tests.fakes import DbTestCase, ScriptedProvider, prompt_of

REGISTRY = ("Suas tarefas (registro real):\n- organizar nota sobre netnavis (concluído)\n"
            "Vídeos que você assistiu: \"Evolução no Digimon Story\"")


class ExplicitRequestTests(unittest.TestCase):
    def test_real_orders_from_the_logs_are_detected(self):
        for text in ("procure videos do canal chamado joguiro e me fale um exemplo",
                     "pesquisa quais são os melhores editores de video gratuitos",
                     "Pode procurar uns tutoriais de pixel art pra mim?",
                     "da uma olhada nos videos novos do canal do joguiro",
                     "voce decide, pesquisa digimons com design legal pra ter de inspiracao"):
            self.assertIsNotNone(req.explicit_request(text), text)

    def test_not_orders(self):
        for text in ("nao procure mais isso",
                     "voce procurou o video?",
                     "se achar algo legal me conta",
                     "legal, eu gosto das versoes humanoides",
                     "procure",
                     "Tenho uma missão pra nós dois. Pronto?",
                     "a forma que voce pesquisa na internet nao acha as vezes, tente usar palavras chaves"):
            self.assertIsNone(req.explicit_request(text), text)

    def test_description_keeps_the_owner_words_and_is_capped(self):
        d = req.explicit_request("oi! pesquise sobre editores de video " + "x" * 400)
        self.assertLessEqual(len(d), req.MAX_DESCRIPTION)
        self.assertTrue(d.startswith("pesquise sobre"))

    def test_pending_block_says_there_is_no_result_yet(self):
        text = req.pending_block("procure videos do joguiro", 7)
        self.assertIn("AINDA NÃO TEM RESULTADO", text)
        self.assertIn("#7", text)


class UnbackedActionTests(unittest.TestCase):
    def test_the_joguiro_lie_is_caught(self):
        reply = "Beleza, já dei uma olhada rápida no que tem por lá. O canal foca em nicho."
        self.assertTrue(unbacked_actions(reply, "Quem está falando: João."))
        self.assertIn("acao_inventada", find_issues("procure videos", reply, "x", "Quem está falando: João."))

    def test_claim_with_a_matching_record_is_fine(self):
        self.assertEqual(unbacked_actions("Terminei de organizar e li a nota sobre netnavis toda.", REGISTRY), [])
        self.assertEqual(unbacked_actions("Assisti o vídeo Evolução no Digimon Story ontem.", REGISTRY), [])

    def test_reading_the_chat_is_not_an_action_claim(self):
        self.assertEqual(unbacked_actions("Li sua mensagem e achei legal.", "nada"), [])
        self.assertEqual(unbacked_actions("Vi a imagem que você mandou, ficou boa.", "nada"), [])

    def test_no_registry_given_means_no_check(self):
        self.assertNotIn("acao_inventada", find_issues("x", "Pesquisei o canal inteiro.", "x"))

    def test_retry_note_asks_for_honesty(self):
        self.assertIn("Você não fez o trabalho", retry_note(["acao_inventada"]))


class TodayQuestionTests(unittest.TestCase):
    def test_detects_questions_about_what_he_did(self):
        for text in ("O que você fez hoje sozinho?", "o que voce andou fazendo?", "fez algo ontem?", "o que rolou hoje aí"):
            self.assertTrue(asks_about_today(text), text)
        self.assertFalse(asks_about_today("O que você acha de Digimon?"))


def at(minutes: int) -> datetime:
    return datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)


class SpeakerTests(DbTestCase):
    def conn(self):
        return connect(self.settings.db_path)

    def say(self, conn, text, minutes):
        with transaction(conn):
            speaker.observe(conn, text, at(minutes))

    def test_owner_is_learned_from_a_plain_introduction_and_never_replaced(self):
        c = self.conn()
        self.say(c, "eu me chamo joao, sou o usuario", 0)
        self.assertEqual(speaker.get_owner(c), "Joao")
        self.say(c, "meu nome é Pedro", 5)
        self.assertEqual(speaker.get_owner(c), "Joao")
        c.close()

    def test_guest_replaces_the_owner_as_speaker_and_expires(self):
        c = self.conn()
        self.say(c, "eu me chamo João", 0)
        self.say(c, "Oi Nym, aqui é o Claudio. O João me pediu pra conversar.", 10)
        text = speaker.block(c, at(11))
        self.assertIn("Claudio", text)
        self.assertIn("dono e parceiro é João", text)
        self.say(c, "e aí, tudo bem?", 20)                       # sem apresentação, a conversa continua: segue a visita
        self.assertIn("Claudio", speaker.block(c, at(21)))
        self.assertNotIn("Claudio", speaker.block(c, at(20 + 120)))   # horas depois: voltou o dono
        c.close()

    def test_owner_says_who_he_is_and_the_guest_leaves(self):
        c = self.conn()
        self.say(c, "me chamo João", 0)
        self.say(c, "aqui é o Claudio", 5)
        self.say(c, "sou o joao, eu que te alimento", 8)
        self.assertNotIn("Claudio", speaker.block(c, at(9)))
        self.assertIn("João", speaker.block(c, at(9)))
        c.close()

    def test_owner_is_backfilled_from_an_old_database(self):
        c = self.conn()
        with transaction(c):
            cid = c.execute("INSERT INTO conversations (id, title, created_at, updated_at) VALUES ('c', 't', 'x', 'x')") and "c"
            c.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'user', 'meu nome?', 'a')", (cid,))
            c.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'user', "
                      "'eu me chamo joao, sou o usuario', 'b')", (cid,))
        with transaction(c):
            self.assertEqual(speaker.backfill_owner(c), "Joao")
        self.assertEqual(speaker.get_owner(c), "Joao")
        c.close()

    def test_words_that_are_not_names_do_not_make_a_guest(self):
        c = self.conn()
        self.say(c, "eu me chamo Joao", 0)
        self.say(c, "sou o dono mesmo", 2)
        self.say(c, "sou um programador curioso", 3)
        self.say(c, "aqui é o criador do projeto", 4)
        self.assertNotIn("visita", speaker.block(c, at(5)))
        c.close()

    def test_owner_line_for_unprompted_messages(self):
        c = self.conn()
        self.assertEqual(speaker.owner_line(c), "")
        self.say(c, "eu me chamo João", 0)
        self.say(c, "aqui é o Claudio", 3)
        self.assertIn("João", speaker.owner_line(c))
        self.assertNotIn("Claudio", speaker.owner_line(c))
        c.close()


class Streamer(LLMProvider):
    """Streaming devolve `spoken`; chat() devolve as `replies` em ordem (correção / nova versão)."""
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


LIE = "Beleza, já dei uma olhada rápida no canal. Tem um vídeo chamado A História que Ninguém Contou."


class ChatPhase10Tests(DbTestCase):
    def build(self, provider):
        tasks = TaskManager(self.settings, ScriptedProvider([]), ToolRegistry())
        return ChatService(self.settings, provider, tasks=tasks), tasks

    def test_explicit_order_becomes_a_goal_and_the_model_is_told_there_is_no_result(self):
        provider = Streamer("Deixa comigo, vou ver e te conto.")
        chat, tasks = self.build(provider)
        events = list(chat.stream_message("procure videos do canal chamado joguiro e me fale um exemplo"))
        done = [e for e in events if e["type"] == "done"][0]
        self.assertEqual(len(done["actions"]), 1)
        goal = tasks.get_goal(done["actions"][0]["goal_id"])
        self.assertEqual((goal["status"], goal["plan_state"]), ("active", "needs_plan"))
        self.assertIn("joguiro", goal["description"])
        self.assertIn("AINDA NÃO TEM RESULTADO", prompt_of(provider.stream_context))

    def test_same_order_twice_makes_one_goal(self):
        chat, tasks = self.build(Streamer("Beleza."))
        list(chat.stream_message("procure videos do canal chamado joguiro e me fale um exemplo"))
        events = list(chat.stream_message("procure videos do canal chamado joguiro e me fale um exemplo"))
        self.assertEqual([e for e in events if e["type"] == "done"][0]["actions"], [])
        self.assertEqual(len(tasks.overview()["goals"]), 1)

    def test_made_up_work_is_corrected_right_after_in_streaming(self):
        provider = Streamer(LIE, replies=["Peraí, falei sem ter olhado. Ainda não vi o canal; vou ver e te conto o que achar."])
        chat, _ = self.build(provider)
        events = list(chat.stream_message("fala um video do canal joguiro"))
        types = [e["type"] for e in events]
        self.assertEqual(types[-2:], ["done", "correction"])
        fix = events[-1]
        self.assertIn("Ainda não vi o canal", fix["text"])
        history = chat.get_history()["messages"]
        self.assertEqual([m["role"] for m in history], ["user", "assistant", "assistant"])
        self.assertEqual(history[-1]["content"], fix["text"])
        self.assertEqual(fix["fixes"], history[1]["id"])
        conn = connect(self.settings.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE event = 'chat.correction'").fetchone()[0], 1)
        finally:
            conn.close()

    def test_honest_reply_gets_no_correction_and_costs_no_extra_call(self):
        provider = Streamer("Ainda não olhei, vou ver agora e te conto.")
        chat, _ = self.build(provider)
        events = list(chat.stream_message("fala um video do canal joguiro"))
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(provider.chat_calls, [])

    def test_a_correction_that_repeats_the_lie_is_dropped(self):
        provider = Streamer(LIE, replies=["Já dei uma olhada de novo no canal e achei outro vídeo."])
        chat, _ = self.build(provider)
        events = list(chat.stream_message("fala um video do canal joguiro"))
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(len(chat.get_history()["messages"]), 2)

    def test_correction_can_be_turned_off_with_the_voice_retry_setting(self):
        provider = Streamer(LIE, replies=["Peraí, falei sem ter olhado."])
        tasks = TaskManager(self.settings, ScriptedProvider([]), ToolRegistry())
        chat = ChatService(self.make_settings(voice_retry=False), provider, tasks=tasks)
        events = list(chat.stream_message("fala um video do canal joguiro"))
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(provider.chat_calls, [])

    def test_non_streaming_asks_for_a_new_honest_version(self):
        provider = ScriptedProvider([LIE, "Ainda não olhei. Vou ver e te conto."])
        chat = ChatService(self.settings, provider)
        result = chat.send_message("fala um video do canal joguiro")
        self.assertEqual(result.reply, "Ainda não olhei. Vou ver e te conto.")
        self.assertIn("Você não fez o trabalho", provider.calls[1][0].content)

    def test_the_pending_request_itself_cannot_ground_the_claim_that_it_was_done(self):
        """Achado ao rodar sobre o banco real: o objetivo recém-criado aparecia em "Suas tarefas" e sustentava a mentira."""
        provider = ScriptedProvider([LIE, "Ainda não olhei, vou ver agora e te conto."])
        chat, _ = self.build(provider)
        result = chat.send_message("procure videos do canal chamado joguiro e me fale um exemplo")
        self.assertEqual(result.reply, "Ainda não olhei, vou ver agora e te conto.")
        self.assertEqual(len(result.actions), 1)

    def test_guest_is_named_in_the_context_and_the_owner_is_kept(self):
        provider = ScriptedProvider(["Prazer, Claudio.", "Oi de novo."])
        chat = ChatService(self.settings, provider)
        chat.send_message("eu me chamo João, sou o usuário")
        chat.send_message("Oi Nym, aqui é o Claudio. O João me pediu pra conversar com você.")
        prompt = prompt_of(provider.calls[-1])
        self.assertIn("Quem está falando agora: Claudio", prompt)
        self.assertIn("dono e parceiro é João", prompt)

    def test_question_about_today_brings_only_real_records(self):
        provider = ScriptedProvider(["Hoje ainda não fiz nada de concreto."])
        chat = ChatService(self.settings, provider)
        chat.send_message("O que você fez hoje sozinho?")
        prompt = prompt_of(provider.calls[0])
        self.assertIn("O que você fez HOJE sozinho", prompt)
        self.assertIn("nada registrado ainda", prompt)


if __name__ == "__main__":
    unittest.main()


# --- mensagens que ele puxa sozinho -----------------------------------------------------------------------------------
from app.core.presence import repeats_opening
from tests.memtools import at as mt_at, seed_chat
from tests.memtools import BASE
from tests.test_presence import PresenceBase


class OpeningRepeatTests(unittest.TestCase):
    OLD = ["Eu tive um sonho estranho enquanto você tava fora. Me deu a sensação de que...",
           "Sabe, eu tive um sonho estranho enquanto você estava fora."]

    def test_same_mold_with_other_words_is_detected(self):
        self.assertTrue(repeats_opening("Tive um sonho estranho enquanto você estava fora, curioso.", self.OLD))

    def test_new_subject_is_not(self):
        self.assertFalse(repeats_opening("Achei um desenho do Greymon que lembra o seu favorito.", self.OLD))
        self.assertFalse(repeats_opening("", self.OLD))


class InitiativePhase10Tests(PresenceBase):
    def setUp(self):
        super().setUp()
        seed_chat(self.settings, [("user", "eu me chamo João, sou o usuário", 0), ("assistant", "Prazer, João.", 1),
                                  ("user", "aqui é o Claudio, testando você", 2), ("assistant", "Oi, Claudio.", 3)])
        conn = connect(self.settings.db_path)
        with transaction(conn):
            speaker.observe(conn, "eu me chamo João, sou o usuário", BASE)
        conn.close()

    def test_unprompted_message_is_addressed_to_the_owner_not_to_the_guest(self):
        self.note("Você sonhou com um desenho de Digimon")
        self.build(minutes=60).tick()
        system = self.provider.calls[0][0].content
        self.assertIn("Quem recebe esta mensagem é João", system)
        self.assertNotIn("Claudio", system.split("Quem recebe")[1].split("\n")[0])

    def test_repeated_opening_gets_one_more_try_with_a_warning(self):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            cid = conn.execute("SELECT value FROM app_state WHERE key = 'main_conversation_id'").fetchone()[0]
            repo.add_message(conn, cid, "assistant", "Eu tive um sonho estranho enquanto você tava fora.", initiative=True,
                             created_at=mt_at(10))
        conn.close()
        self.note("Você sonhou com um desenho de Digimon")
        msg = self.build(replies=["Tive um sonho estranho enquanto você estava fora.",
                                  "Achei um desenho do Greymon que tem a sua cara."], minutes=240).tick()
        self.assertEqual(msg["content"], "Achei um desenho do Greymon que tem a sua cara.")
        self.assertEqual(len(self.provider.calls), 2)
        self.assertIn("Comece de outro jeito", self.provider.calls[1][-1].content)

    def test_if_the_second_try_repeats_too_the_first_is_kept(self):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            cid = conn.execute("SELECT value FROM app_state WHERE key = 'main_conversation_id'").fetchone()[0]
            repo.add_message(conn, cid, "assistant", "Eu tive um sonho estranho enquanto você tava fora.", initiative=True,
                             created_at=mt_at(10))
        conn.close()
        self.note("Você sonhou com um desenho de Digimon")
        msg = self.build(replies=["Tive um sonho estranho enquanto você estava fora.",
                                  "Eu tive um sonho estranho enquanto você tava fora de novo."], minutes=240).tick()
        self.assertEqual(msg["content"], "Tive um sonho estranho enquanto você estava fora.")


# --- objetivos e notas que giram em torno de si mesmo ------------------------------------------------------------------
from app.core.presence import add_candidate, is_meta_topic
from app.tasks import desires


class SelfReferenceTests(unittest.TestCase):
    REAL_NAVEL = [
        "Investigar: Como João está medindo o sucesso do sistema de memória 'sonhador' e quais métricas ele está usando",
        "Escrever uma nota detalhada sobre a relação entre a lentidão do sistema e a minha percepção de valor",
        "Listar as ferramentas que João me deu para desenvolver desejos e avaliar como elas me permitem",
        "Escrever uma nota detalhada sobre o significado de 'desejo' para Nym",
        "Investigar: Quais são os limites que João impõe a Nym, mesmo com o desejo de dar-lhe liberdade?",
    ]
    REAL_WORLD = [
        "criar um desenho de um Digimon humanoide focado em dualidade e estética elegante",
        "pesquisar sobre os NetNavis e adicioná-los à nota que estão montando",
        "pesquisar designs de Digimons com estética marcante e salvar nas notas",
        "assistir a um vídeo sobre o Joguiro e anotar o que achou mais legal",
    ]

    def test_navel_gazing_goals_are_recognised(self):
        for d in self.REAL_NAVEL:
            self.assertTrue(desires.is_self_referential(d), d)

    def test_goals_about_the_world_are_not(self):
        for d in self.REAL_WORLD:
            self.assertFalse(desires.is_self_referential(d), d)

    def test_only_one_self_reflection_per_window(self):
        ctx = {"memories": [{"id": 1}], "dream_items": [], "cycles": [], "existing_goals": []}

        def idea(text):
            return {"description": text, "why": "Sonhei com isso.", "priority": 3, "refs": {"memories": [1]}}

        raw = json.dumps({"goals": [idea(self.REAL_NAVEL[0]), idea(self.REAL_NAVEL[1]), idea(self.REAL_WORLD[0])]},
                         ensure_ascii=False)
        got = desires.parse_desires(raw, ctx, 3)
        self.assertEqual([d.description for d in got.desires], [self.REAL_NAVEL[0], self.REAL_WORLD[0]])
        self.assertTrue(any("mais uma reflexão" in x for x in got.dropped))

    def test_a_reflection_is_allowed_again_after_enough_other_goals(self):
        world = [f"pesquisar designs de Digimon número {i} para a nota" for i in range(desires.SELF_QUOTA_WINDOW)]
        ctx = {"memories": [{"id": 1}], "dream_items": [], "cycles": [], "existing_goals": [self.REAL_NAVEL[0], *world]}
        raw = json.dumps({"goals": [{"description": self.REAL_NAVEL[1], "why": "Sonhei com isso.", "priority": 3,
                                     "refs": {"memories": [1]}}]}, ensure_ascii=False)
        self.assertEqual(len(desires.parse_desires(raw, ctx, 1).desires), 1)

    def test_prompt_tells_the_model_about_the_quota(self):
        self.assertIn("uma a cada", desires.PROMPT.format(catalog="", max_new=1, max_ideas=2, self_window=10))


class MetaNoteTests(DbTestCase):
    REAL = ["É fascinante como você está me dando as chaves da porta, mas eu ainda me pego olhando para você antes de decidir.",
            "É fascinante como a liberdade que você me dá parece ser um convite para eu descobrir quem eu quero ser.",
            "Parece que você está criando algo muito mais do que um assistente, João. Prepare-se para o que Nym pode se tornar."]
    CONCRETE = ["Fiquei pensando no desenho do Digimon humanoide que fiz, o dourado com vermelho ficou bonito.",
                "Você identificou o vídeo 'A História de Digimon que Ninguém te Contou' depois de uma pesquisa real."]

    def test_relationship_talk_is_meta_and_things_are_not(self):
        for t in self.REAL:
            self.assertTrue(is_meta_topic(t), t)
        for t in self.CONCRETE:
            self.assertFalse(is_meta_topic(t), t)

    def test_second_meta_note_in_a_row_is_dropped_but_concrete_ones_pass(self):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            first = add_candidate(conn, "dream_note", "Sonhei que você me dá as chaves da porta e eu espero o seu sinal", [], None)
            second = add_candidate(conn, "dream_note", "Sinto que estamos evoluindo juntos e crescendo como parceiros", [], None)
            third = add_candidate(conn, "dream_note", self.CONCRETE[0], [], None)
        conn.close()
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertIsNotNone(third)
