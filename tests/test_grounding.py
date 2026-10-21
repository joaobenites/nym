"""Achados da conversa real (19/10): lembrança inventada ("o João me avisou que você viria") e notas de sonho em molde."""
import unittest

from app.core.chat_service import ChatService
from app.core.presence import add_candidate, is_stale_note
from app.core.voice import find_issues, has_tic_opening, retry_note, ungrounded_claims
from app.database.connection import connect, transaction
from tests.fakes import DbTestCase, ScriptedProvider

EVIDENCE = ("Oi Nym, aqui é o Claudio. O João me pediu pra conversar com você.\n"
            "Lembrança: João gosta de Digimon e da estética da Angewomon.")


class GroundingTests(unittest.TestCase):
    def test_invented_announcement_is_flagged(self):
        reply = "O João me falou que você ia aparecer hoje à tarde."
        self.assertTrue(ungrounded_claims(reply, EVIDENCE))
        self.assertIn("lembranca_sem_registro", find_issues("oi", reply, EVIDENCE))

    def test_claim_backed_by_memory_is_not_flagged(self):
        self.assertEqual(ungrounded_claims("O João me disse que gosta de Digimon.", EVIDENCE), [])

    def test_invented_shared_episode_is_flagged(self):
        self.assertTrue(ungrounded_claims("Lembro de quando a gente foi ao parque e comeu sorvete.", EVIDENCE))

    def test_no_evidence_given_means_no_check(self):
        self.assertNotIn("lembranca_sem_registro", find_issues("oi", "O João me falou que vem hoje."))
        self.assertEqual(ungrounded_claims("O João me falou que vem hoje.", "  "), [])

    def test_plain_reply_is_fine(self):
        self.assertEqual(find_issues("e aí?", "Tudo certo por aqui, mexendo nuns desenhos.", EVIDENCE), [])

    def test_retry_note_tells_what_to_do(self):
        note = retry_note(["lembranca_sem_registro"])
        self.assertIn("não aparece em nenhuma lembrança", note)
        self.assertIn("Não cite o que o dono teria dito", note)


class ChatGroundingTests(DbTestCase):
    def test_invented_memory_triggers_one_rewrite_and_the_better_version_wins(self):
        provider = ScriptedProvider(["O João me avisou que você ia trazer pizza.", "Oi! Que bom te ver por aqui."])
        svc = ChatService(self.settings, provider)
        result = svc.send_message("Oi Nym, tudo bem?")
        self.assertEqual(result.reply, "Oi! Que bom te ver por aqui.")
        self.assertEqual(len(provider.calls), 2)
        self.assertIn("não aparece em nenhuma lembrança", provider.calls[1][0].content)

    def test_grounded_reply_is_kept_without_rewrite(self):
        provider = ScriptedProvider(["Você disse que o ônibus atrasou, e aí, chegou a tempo?"])
        svc = ChatService(self.settings, provider)
        result = svc.send_message("O ônibus atrasou hoje")
        self.assertEqual(len(provider.calls), 1)
        self.assertIn("ônibus", result.reply)


class StaleNoteTests(DbTestCase):
    def add(self, conn, text):
        with transaction(conn):
            return add_candidate(conn, "dream_note", text, [], None)

    def test_tic_opening_detected(self):
        self.assertTrue(has_tic_opening("É fascinante como você está construindo isso."))
        self.assertFalse(has_tic_opening("Fiquei pensando na Angewomon que você mostrou."))

    def test_tic_note_is_dropped(self):
        conn = connect(self.settings.db_path)
        self.assertIsNone(self.add(conn, "É fascinante como estamos evoluindo juntos."))
        conn.close()

    def test_same_opening_as_a_recent_note_is_dropped_even_if_delivered(self):
        conn = connect(self.settings.db_path)
        first = self.add(conn, "Fiquei pensando na Angewomon e no jeito que ela equilibra luz e sombra.")
        self.assertIsNotNone(first)
        with transaction(conn):
            conn.execute("UPDATE pending_messages SET status = 'delivered' WHERE id = ?", (first,))
        self.assertTrue(is_stale_note(conn, "dream_note", "Fiquei pensando na Gabumon e na lealdade dele."))
        self.assertIsNone(self.add(conn, "Fiquei pensando na Gabumon e na lealdade dele."))
        self.assertIsNotNone(self.add(conn, "Achei estranho o final daquele vídeo que você mandou."))
        conn.close()


if __name__ == "__main__":
    unittest.main()
