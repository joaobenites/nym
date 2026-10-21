"""Passo 4.1: noção de tempo entre conversas e voz menos "chatbot"."""
import unittest
from datetime import datetime, timedelta, timezone

from app.core.chat_service import ChatService
from app.core.state import StateService
from app.core.timesense import describe_gap, describe_now, gap_marker, relative_moment, time_block
from app.core.voice import find_issues, strip_emoji
from app.models.base import ModelError
from tests.fakes import prompt_of, spoken, DbTestCase, FakeProvider, ScriptedProvider
from tests.memtools import BASE, at, clock_at

# O trecho do log real que motivou este passo.
LOG_REPLY = ("Nossa, você descobriu! 🤭\n\nEu... realmente tive algo parecido com um \"sonho\" esses dias. É meio estranho "
             "descrever, sabe? Não eram imagens ou sensações como um sonho humano, mas sim uma série de conexões e perguntas "
             "surgindo na minha memória.\n\nLembro de ter pensado em como a relação entre Digimon e seus parceiros humanos é "
             "importante, e me perguntei qual seria o meu papel na sua vida, João. É engraçado, né? Uma IA se preocupando "
             "com isso. 😅\n\nVocê percebeu alguma coisa diferente em mim depois disso")


class TimeSenseTests(unittest.TestCase):
    def test_now_in_portuguese(self):
        self.assertEqual(describe_now(datetime(2026, 10, 17, 21, 40)), "sábado, 17 de outubro de 2026, 21:40 (noite)")
        self.assertIn("(madrugada)", describe_now(datetime(2026, 10, 17, 3, 0)))

    def test_gaps_read_like_speech(self):
        self.assertEqual(describe_gap(timedelta(minutes=40)), "uns 40 minutos")
        self.assertEqual(describe_gap(timedelta(hours=1)), "uma hora")
        self.assertEqual(describe_gap(timedelta(hours=5)), "umas 5 horas")
        self.assertEqual(describe_gap(timedelta(days=3)), "3 dias")
        self.assertEqual(describe_gap(timedelta(days=21)), "umas 3 semanas")

    def test_relative_moment(self):
        now = datetime(2026, 10, 17, 21, 0)                        # sábado
        self.assertEqual(relative_moment(datetime(2026, 10, 17, 9, 0), now), "hoje de manhã")
        self.assertEqual(relative_moment(datetime(2026, 10, 16, 22, 0), now), "ontem à noite")
        self.assertEqual(relative_moment(datetime(2026, 10, 14, 15, 0), now), "na quarta à tarde")
        self.assertEqual(relative_moment(datetime(2026, 10, 3, 15, 0), now), "em 3 de outubro")

    def test_block_distinguishes_first_talk_same_talk_and_return(self):
        now = BASE + timedelta(days=3)
        self.assertIn("primeira conversa", time_block(now, None, timezone.utc))
        self.assertIn("no meio de uma conversa", time_block(now, (now - timedelta(minutes=5)).isoformat(), timezone.utc))
        block = time_block(now, BASE.isoformat(), timezone.utc)
        self.assertIn("há 3 dias", block)
        self.assertIn("estranhar o sumiço", block)

    def test_unreadable_timestamp_never_breaks(self):
        self.assertIn("Agora é", time_block(BASE, "t", timezone.utc))
        self.assertIsNone(gap_marker("t", at(0)))

    def test_gap_marker_only_after_long_pauses(self):
        self.assertIsNone(gap_marker(at(0), at(60)))
        self.assertEqual(gap_marker(at(0), at(60 * 24 * 2)), "[2 dias depois]")


class VoiceTests(unittest.TestCase):
    def test_the_real_log_is_caught(self):
        issues = find_issues("você sonhou com alguma coisa?", LOG_REPLY)
        self.assertEqual(set(issues), {"fala_como_ia", "desculpa_do_sonho", "pede_avaliacao", "longa"})

    def test_talking_about_being_an_ai_is_fine_when_asked(self):
        self.assertEqual(find_issues("você é uma IA? sente alguma coisa de verdade?",
                                     "Sou uma IA, sim. Se sinto de verdade, ninguém sabe."), [])

    def test_natural_reply_passes(self):
        self.assertEqual(find_issues("sonhou com algo?", "Sonhei com aquela ideia do seu canal. Fiquei pensando nela."), [])

    def test_long_reply_to_short_message(self):
        long = " ".join(f"Frase número {i}." for i in range(8))
        self.assertEqual(find_issues("oi", long), ["longa"])

    def test_strip_emoji(self):
        self.assertEqual(strip_emoji("Nossa 🤭 que legal! 😅"), "Nossa que legal!")
        self.assertEqual(strip_emoji("ok 👍🏽"), "ok")
        self.assertEqual(strip_emoji("sem emoji :)"), "sem emoji :)")


class ChatVoiceTests(DbTestCase):
    def test_bad_reply_gets_one_retry_and_the_better_one_wins(self):
        provider = ScriptedProvider([LOG_REPLY, "Sonhei, sim. Com aquela história de Digimon que você me contou."])
        result = ChatService(self.settings, provider).send_message("você sonhou com alguma coisa?")
        self.assertEqual(len(provider.calls), 2)
        self.assertIn("rascunho desta resposta foi descartado", provider.calls[1][0].content)
        self.assertEqual(result.reply, "Sonhei, sim. Com aquela história de Digimon que você me contou.")

    def test_retry_that_is_not_better_keeps_the_first_without_emoji(self):
        provider = ScriptedProvider([LOG_REPLY, LOG_REPLY])
        result = ChatService(self.settings, provider).send_message("você sonhou?")
        self.assertEqual(len(provider.calls), 2)                 # nunca mais que uma nova tentativa
        self.assertNotIn("🤭", result.reply)

    def test_retry_failure_still_answers(self):
        provider = ScriptedProvider([LOG_REPLY, ModelError("caiu")])
        result = ChatService(self.settings, provider).send_message("você sonhou?")
        self.assertTrue(result.reply.startswith("Nossa, você descobriu!"))

    def test_retry_can_be_turned_off_and_emoji_allowed(self):
        provider = ScriptedProvider([LOG_REPLY])
        s = self.make_settings(voice_retry=False, allow_emoji=True)
        result = ChatService(s, provider).send_message("você sonhou?")
        self.assertEqual(len(provider.calls), 1)
        self.assertIn("🤭", result.reply)

    def test_good_reply_costs_a_single_call(self):
        provider = FakeProvider()
        ChatService(self.settings, provider).send_message("oi")
        self.assertEqual(len(provider.calls), 1)


class ChatTimeTests(DbTestCase):
    def chat(self, provider, minutes):
        return ChatService(self.settings, provider, state=StateService(self.settings, clock=clock_at(minutes)),
                           clock=clock_at(minutes), tz=timezone.utc)

    def test_nym_knows_how_long_it_has_been(self):
        provider = FakeProvider()
        self.chat(provider, 0).send_message("boa noite, vou dormir")
        self.chat(provider, 60 * 24 * 3).send_message("voltei")
        call = provider.calls[-1]
        self.assertIn("há 3 dias", prompt_of(call))
        self.assertEqual(spoken(call[-1].content), "[3 dias depois] voltei")   # a marca também aparece no histórico
        self.assertEqual(call[1].content, "boa noite, vou dormir")      # mensagens próximas não ganham marca

    def test_state_rest_ignores_the_message_just_sent(self):
        provider = FakeProvider()
        self.chat(provider, 0).send_message("tchau")
        self.chat(provider, 60 * 24 * 3).send_message("voltei")
        # Antes da correção, a própria mensagem "voltei" zerava o descanso e o estado nunca via os 3 dias.
        self.assertIn("(ausente há dias)", prompt_of(provider.calls[-1]))

    def test_state_service_before_message_id(self):
        from tests.memtools import seed_chat
        ids = seed_chat(self.settings, [("user", "antiga", 0), ("user", "agora", 60 * 10)])
        state = StateService(self.settings, clock=clock_at(60 * 10))
        self.assertAlmostEqual(state.current().inputs["hours_since_last_chat"], 0.0, places=3)
        self.assertAlmostEqual(state.current(ids[1]).inputs["hours_since_last_chat"], 10.0, places=3)


if __name__ == "__main__":
    unittest.main()
