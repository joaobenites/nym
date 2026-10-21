import unittest

from app.memory.text import is_duplicate, is_superset, looks_sensitive, normalize, token_set


class TextTests(unittest.TestCase):
    def test_normalize_strips_accents_and_punctuation(self):
        self.assertEqual(normalize("Olá, Pão-de-Açúcar!"), "ola pao de acucar")

    def test_plural_and_stopwords(self):
        self.assertEqual(token_set("os gatos"), token_set("gato"))
        self.assertNotIn("usuario", token_set("O usuário gosta de café"))

    def test_duplicates_ignore_punctuation_and_case(self):
        self.assertTrue(is_duplicate("O usuário tem um gato chamado Mia", "o usuário tem um gato, chamado MIA."))

    def test_different_entities_are_not_duplicates(self):
        self.assertFalse(is_duplicate("O usuário tem um gato chamado Mia", "O usuário tem um gato chamado Bob"))

    def test_negation_blocks_duplicate(self):
        self.assertFalse(is_duplicate("O usuário gosta de café", "O usuário não gosta de café"))
        self.assertFalse(is_duplicate("O usuário bebe café com açúcar", "O usuário bebe café sem açúcar"))

    def test_different_numbers_block_duplicate(self):
        self.assertFalse(is_duplicate("Mora em Lisboa desde 2020", "Mora em Lisboa desde 2021"))

    def test_superset_detects_extra_information(self):
        self.assertTrue(is_superset("Gosta de café preto e de pão", "Gosta de café preto"))
        self.assertFalse(is_superset("Gosta de café preto", "Gosta de café preto e de pão"))

    def test_sensitive_detection(self):
        for text in ("meu cpf é 123.456.789-09", "a senha do wifi é abc123", "cartão 4111 1111 1111 1111",
                     "conta 123456789012", "minha api key é xyz"):
            self.assertTrue(looks_sensitive(text), text)
        for text in ("tenho 3 gatos", "nasci em 1990", "moro no apartamento 42"):
            self.assertFalse(looks_sensitive(text), text)


if __name__ == "__main__":
    unittest.main()
