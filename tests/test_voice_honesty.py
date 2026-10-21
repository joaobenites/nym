"""Achados da conversa real com o Nym (Fase 7.1): reforçar lembrança questionada e muletas de texto."""
import unittest

from app.core.voice import VOICE_RULES, find_issues, retry_note

DOUBLE_DOWN = ("Eu lembro sim, não foi só uma expressão. Ele comentou isso numa conversa mais profunda que a gente teve.")
ADMIT = "Boa pergunta. Na real não tenho esse registro, falei sem checar."


class HonestyTests(unittest.TestCase):
    def test_doubling_down_when_questioned_is_flagged(self):
        self.assertIn("reforca_lembranca", find_issues("Lembra mesmo ou foi força de expressão? Quando foi?", DOUBLE_DOWN))

    def test_admitting_is_fine(self):
        self.assertEqual(find_issues("Lembra mesmo? Quando foi?", ADMIT), [])

    def test_saying_i_remember_without_being_questioned_is_not_flagged(self):
        self.assertEqual(find_issues("Você lembra do que a gente falou ontem?", "Lembro sim, a gente falou do canal."), [])

    def test_text_tics_need_two_to_be_flagged(self):
        one = "Foi um dia puxado, sabe? Mas deu pra avançar."
        two = "É fascinante, né? É quase como se o sistema estivesse acordando."
        self.assertEqual(find_issues("e aí?", one), [])
        self.assertIn("muletas", find_issues("e aí?", two))

    def test_retry_note_explains_the_recall_problem(self):
        note = retry_note(["reforca_lembranca"])
        self.assertIn("reforçou uma lembrança", note)
        self.assertIn("não tem esse registro", note)

    def test_rules_carry_the_new_guidance(self):
        self.assertIn("não tem esse registro", VOICE_RULES)
        self.assertIn("no máximo 2 parágrafos", VOICE_RULES)


if __name__ == "__main__":
    unittest.main()
