import unittest

from app.dreams.associations import MAX_HYPOTHESES, build_messages, parse_dream
from app.memory.extraction import ExtractionError
from tests.dreamtools import dream_reply

VALID = {1, 2, 3}


class ParseTests(unittest.TestCase):
    def test_valid_result(self):
        r = parse_dream(dream_reply(
            associations=[{"memories": [1, 2], "text": "Os dois falam de gatos", "confidence": 0.7}],
            hypotheses=[{"text": "Talvez o usuário goste de animais", "evidence": [1], "confidence": 0.9}],
            questions=[{"text": "Qual a idade da Mia?", "evidence": [1]}],
            exploration=[{"question": "Como cuidar de gatos idosos?", "why": "o gato é citado", "evidence": [1]}],
            note={"text": "Como está a Mia?", "evidence": [1]}), VALID)
        self.assertEqual(len(r.associations), 1)
        self.assertEqual(r.dream_note, ("Como está a Mia?", [1]))
        self.assertLessEqual(r.hypotheses[0].confidence, 0.6)       # hipótese nunca vira certeza

    def test_ids_outside_the_selection_are_dropped(self):
        r = parse_dream(dream_reply(
            associations=[{"memories": [1, 99], "text": "liga um real a um inventado"}],     # sobra 1 id: não é associação
            hypotheses=[{"text": "sem lastro real", "evidence": [99]}],
            questions=[{"text": "pergunta sem lastro", "evidence": []}],
            note={"text": "nota sem lastro", "evidence": [77]}), VALID)
        self.assertEqual((r.associations, r.hypotheses, r.open_questions, r.dream_note), ([], [], [], None))

    def test_association_needs_two_distinct_memories(self):
        r = parse_dream(dream_reply(associations=[{"memories": [2, 2], "text": "repetida"}]), VALID)
        self.assertEqual(r.associations, [])

    def test_secrets_are_dropped(self):
        r = parse_dream(dream_reply(hypotheses=[{"text": "A senha do usuário é 1234", "evidence": [1]},
                                                {"text": "Talvez goste de café", "evidence": [1]}]), VALID)
        self.assertEqual([h.text for h in r.hypotheses], ["Talvez goste de café"])

    def test_counts_are_limited(self):
        many = [{"text": f"Hipótese número {i} distinta", "evidence": [1]} for i in range(MAX_HYPOTHESES + 5)]
        self.assertEqual(len(parse_dream(dream_reply(hypotheses=many), VALID).hypotheses), MAX_HYPOTHESES)

    def test_bad_output_raises(self):
        for raw in ("não é json", "", "[]", '{"associations": []}', '{"review": "  "}', '{"review": "ok", "associations": "x"}'):
            with self.assertRaises(ExtractionError, msg=raw):
                parse_dream(raw, VALID)

    def test_prompt_lists_real_memories_and_open_questions(self):
        msgs = build_messages([{"id": 5, "kind": "semantic", "epistemic": "hypothesis", "occurred_at": "2026-10-01T10:00:00+00:00",
                                "content": "O usuário talvez goste de jazz"}], ["Qual banda?"])
        self.assertIn("[5] (fato, hipótese, 2026-10-01)", msgs[1].content)
        self.assertIn("Qual banda?", msgs[1].content)


if __name__ == "__main__":
    unittest.main()
