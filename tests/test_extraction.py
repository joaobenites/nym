import json
import unittest

from app.memory.extraction import (
    MAX_FACTS,
    ExtractionError,
    build_messages,
    parse_extraction,
)
from tests.memtools import extraction, fact

ALL = {1, 2, 3, 4}
USER = {1, 3}


def parse(raw, existing=()):
    return parse_extraction(raw, message_ids=ALL, user_ids=USER, existing_ids=set(existing))


class ExtractionTests(unittest.TestCase):
    def test_valid_extraction(self):
        data = parse(extraction(facts=[fact("O usuário tem um gato chamado Mia", [1])], outcome="nenhum"))
        self.assertEqual(len(data.facts), 1)
        self.assertTrue(data.facts[0].stated)
        self.assertEqual(data.outcome, "nenhum")

    def test_accepts_fenced_and_noisy_json(self):
        raw = "Claro! Aqui está:\n```json\n" + extraction(facts=[fact("O usuário mora em Lisboa", [1])]) + "\n```\nPronto."
        self.assertEqual(len(parse(raw).facts), 1)

    def test_garbage_and_missing_summary_raise(self):
        for raw in ("isso não é json", "", "[1, 2, 3]", json.dumps({"facts": []}), json.dumps({"summary": "  "})):
            with self.assertRaises(ExtractionError, msg=raw):
                parse(raw)

    def test_items_without_valid_evidence_are_dropped(self):
        data = parse(extraction(facts=[
            fact("Sem evidência alguma", []),
            fact("Evidência inventada", [99]),
            fact("Evidência real", [1]),
        ]))
        self.assertEqual([f.text for f in data.facts], ["Evidência real"])

    def test_stated_requires_user_evidence_otherwise_it_is_a_hypothesis(self):
        data = parse(extraction(facts=[fact("O usuário gosta de jazz", [2], kind="stated", confidence=0.99)]))
        item = data.facts[0]   # a mensagem 2 é do Nym, não do usuário
        self.assertFalse(item.stated)
        self.assertLessEqual(item.confidence, 0.7)

    def test_inferred_confidence_is_capped(self):
        data = parse(extraction(facts=[fact("O usuário parece gostar de jazz", [1], kind="inferred", confidence=1.0)]))
        self.assertFalse(data.facts[0].stated)
        self.assertLessEqual(data.facts[0].confidence, 0.7)

    def test_secrets_are_dropped(self):
        data = parse(extraction(facts=[
            fact("A senha do usuário é abc123", [1]),
            fact("O CPF do usuário é 123.456.789-09", [1]),
            fact("O usuário tem 3 gatos", [1]),
        ]))
        self.assertEqual([f.text for f in data.facts], ["O usuário tem 3 gatos"])

    def test_counts_are_limited(self):
        many = [fact(f"Fato diferente número {i} sobre algo {i}", [1]) for i in range(MAX_FACTS + 5)]
        self.assertEqual(len(parse(extraction(facts=many)).facts), MAX_FACTS)

    def test_contradicts_only_keeps_ids_that_were_offered(self):
        data = parse(extraction(facts=[fact("O usuário mora em Braga", [1], contradicts=[7, 42])]), existing={7})
        self.assertEqual(data.facts[0].contradicts, [7])

    def test_identity_notes_are_never_stated(self):
        data = parse(extraction(notes=[{"text": "O usuário prefere respostas curtas", "evidence": [1], "confidence": 0.95}]))
        note = data.identity_notes[0]
        self.assertFalse(note.stated)
        self.assertLessEqual(note.confidence, 0.7)

    def test_prompt_contains_ids_and_existing_memories(self):
        rows = [{"id": 1, "role": "user", "content": "oi"}, {"id": 2, "role": "assistant", "content": "e aí"}]
        msgs = build_messages(rows, [{"id": 7, "content": "O usuário mora no Porto"}])
        self.assertEqual(msgs[0].role, "system")
        self.assertIn("[1] Usuário: oi", msgs[1].content)
        self.assertIn("[2] Nym: e aí", msgs[1].content)
        self.assertIn("7: O usuário mora no Porto", msgs[1].content)


if __name__ == "__main__":
    unittest.main()
