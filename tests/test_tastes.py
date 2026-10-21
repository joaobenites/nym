"""Fase 7.1: gostos e prioridades próprios do Nym (formados só de registros reais)."""
import json
import unittest
from datetime import datetime, timedelta, timezone

from app.core import tastes
from app.core.chat_service import ChatService
from app.core.policy import OPERATIONAL_RULES
from app.database.connection import connect, transaction
from app.tasks import desires
from app.tools.registry import ToolRegistry
from tests.dreamtools import rows
from tests.fakes import DbTestCase, FakeProvider, prompt_of
from tests.memtools import BASE, at, seed_chat
from tests.test_presence import PresenceBase

NOW = BASE + timedelta(days=3)


def iso(days_ago, hours=0):
    return (NOW - timedelta(days=days_ago, hours=hours)).isoformat(timespec="milliseconds")


class TasteBase(DbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = connect(self.settings.db_path)
        self.addCleanup(self.conn.close)

    def research(self, query, days_ago=0, own=True, status="ok"):
        goal = None
        if own:
            goal = self.goal("pesquisar " + query, days_ago=days_ago)
        with transaction(self.conn):
            return self.conn.execute("INSERT INTO research_log (goal_id, kind, query, status, created_at) VALUES (?, 'search', ?, ?, ?)",
                                     (goal, query, status, iso(days_ago))).lastrowid

    def goal(self, text, days_ago=0, origin="nym", status="done", error=None):
        with transaction(self.conn):
            return self.conn.execute("INSERT INTO goals (description, origin, priority, status, created_at, updated_at, last_error) "
                                     "VALUES (?, ?, 3, ?, ?, ?, ?)", (text, origin, status, iso(days_ago), iso(days_ago), error)).lastrowid

    def creation(self, title, about="", days_ago=0, reaction=None):
        with transaction(self.conn):
            cid = self.conn.execute("INSERT INTO creations (kind, title, about, content, created_at) VALUES ('text', ?, ?, 'x', ?)",
                                    (title, about, iso(days_ago))).lastrowid
            if reaction is not None:
                self.conn.execute("INSERT INTO feedback (target, target_id, value, created_at) VALUES ('creation', ?, ?, ?)",
                                  (cid, reaction, iso(days_ago)))
        return cid

    def dream_item(self, text, days_ago=0, kind="hypothesis"):
        with transaction(self.conn):
            cyc = self.conn.execute("SELECT id FROM dream_cycles ORDER BY id DESC LIMIT 1").fetchone()
            cyc = cyc["id"] if cyc else self.conn.execute(
                "INSERT INTO dream_cycles (trigger, status, started_at) VALUES ('idle', 'completed', ?)", (iso(days_ago),)).lastrowid
            return self.conn.execute("INSERT INTO dream_items (cycle_id, kind, content, refs, created_at) VALUES (?, ?, ?, '[]', ?)",
                                     (cyc, kind, text, iso(days_ago))).lastrowid

    def refresh(self, now=NOW):
        with transaction(self.conn):
            return tastes.refresh(self.conn, now)

    def labels(self, status="active"):
        return {t["label"]: t for t in tastes.with_evidence(self.conn, status)}

    def feed_astronomia(self):
        """Registros de vários tipos e dias sobre o mesmo assunto."""
        self.research("buracos negros e astronomia", days_ago=2)
        self.research("astronomia para iniciantes", days_ago=1)
        self.creation("Poema sobre a astronomia", days_ago=1)
        self.goal("escrever sobre astronomia", days_ago=0)
        self.dream_item("A astronomia aparece nas duas conversas", days_ago=0)


class TopicTests(unittest.TestCase):
    def test_extracts_subjects_not_generic_words(self):
        found = tastes.topics_in("Quero pesquisar sobre astronomia e dinossauros, fazendo uma nota")
        self.assertEqual(set(found.values()), {"astronomia", "dinossauros"})

    def test_plural_and_accents_share_a_key(self):
        self.assertEqual(tastes.topic_key("Digimons"), tastes.topic_key("digimon"))
        self.assertEqual(tastes.topic_key("evolução"), tastes.topic_key("EVOLUCAO"))

    def test_verbs_and_adverbs_are_not_subjects(self):
        self.assertEqual(tastes.topics_in("investigar rapidamente entendendo escrever"), {})


class FormationTests(TasteBase):
    def test_nothing_without_evidence(self):
        self.refresh()
        self.assertEqual(self.labels(), {})
        self.assertIn("ainda não se formaram", tastes.render_block(tastes.active(self.conn)))

    def test_a_taste_forms_from_several_kinds_of_record_on_several_days(self):
        self.feed_astronomia()
        out = self.refresh()
        self.assertEqual(out["created"] >= 1, True)
        t = self.labels()["astronomia"]
        self.assertEqual((t["valence"], t["origin"]), (1, "own"))
        self.assertGreaterEqual(t["days"], 2)
        self.assertGreaterEqual(len(t["sources"]), 2)
        self.assertTrue(t["evidence"])
        self.assertTrue(all(e["snippet"] for e in t["evidence"]))
        self.assertIn("astronomia", tastes.render_block(tastes.active(self.conn)))

    def test_dreams_alone_never_form_a_taste_even_when_they_repeat(self):
        for i in range(30):
            self.dream_item(f"A astronomia de novo, vez {i}", days_ago=i % 3)
        self.refresh()
        self.assertNotIn("astronomia", self.labels())

    def test_conversation_alone_is_not_enough(self):
        seed_chat(self.settings, [("user", f"astronomia é incrível {i}", i * 60 * 24) for i in range(6)])
        self.refresh(BASE + timedelta(days=7))
        self.assertEqual(self.labels(), {})

    def test_one_day_is_enough_only_with_strong_recurrence_across_kinds(self):
        for i in range(4):
            self.research(f"física quântica parte {i}", days_ago=0)
        self.creation("Desenho de física quântica", days_ago=0)
        self.goal("explicar física quântica", days_ago=0)
        for i in range(4):
            self.dream_item(f"física quântica ligada a conversa {i}", days_ago=0)
        seed_chat(self.settings, [("user", "estudei física quântica", 5)])
        self.refresh(BASE + timedelta(hours=1))  # tudo no mesmo dia, mas em 5 tipos de fonte
        self.assertIn("quântica", self.labels())

    def test_sensitive_evidence_is_ignored(self):
        self.research("senha do banco 123456789012 astronomia", days_ago=2)
        self.research("senha astronomia", days_ago=1)
        self.creation("Poema de astronomia", days_ago=1)
        self.refresh()
        self.assertNotIn("astronomia", self.labels())

    def test_failed_searches_do_not_count(self):
        self.research("astronomia", days_ago=2, status="error", own=False)
        self.research("astronomia", days_ago=1, status="error", own=False)
        self.creation("astronomia", days_ago=1)
        self.refresh()
        self.assertEqual(self.labels(), {})

    def test_refresh_is_idempotent(self):
        self.feed_astronomia()
        self.refresh()
        before = tastes.with_evidence(self.conn)
        out = self.refresh()
        self.assertEqual(out["created"], 0)
        self.assertEqual([(t["label"], t["strength"], len(t["evidence"])) for t in tastes.with_evidence(self.conn)],
                         [(t["label"], t["strength"], len(t["evidence"])) for t in before])

    def test_user_goals_count_less_than_his_own(self):
        a = tastes.compute(self.conn, NOW)
        self.assertEqual(a, [])
        self.research("tema marte", days_ago=1, own=True)
        self.research("tema venus", days_ago=1, own=False)
        weights = {e.label: e.weight for e in tastes._collect(self.conn) if e.source == "pesquisa"}
        self.assertGreater(weights["marte"], weights["venus"])


class TimeTests(TasteBase):
    def test_taste_fades_when_not_fed(self):
        self.feed_astronomia()
        self.refresh()
        self.assertIn("astronomia", self.labels())
        self.refresh(NOW + timedelta(days=120))
        self.assertNotIn("astronomia", self.labels("active"))
        self.assertIn("astronomia", self.labels("faded"))
        self.assertEqual(self.labels("faded")["astronomia"]["strength"], 0)

    def test_hysteresis_keeps_a_formed_taste_with_less_evidence(self):
        self.feed_astronomia()
        self.refresh()
        strong = self.labels()["astronomia"]["strength"]
        later = self.refresh(NOW + timedelta(days=21))        # uma meia-vida depois
        self.assertIn("astronomia", self.labels())
        self.assertLess(self.labels()["astronomia"]["strength"], strong)

    def test_a_faded_taste_comes_back_when_fed_again(self):
        self.feed_astronomia()
        self.refresh()
        self.refresh(NOW + timedelta(days=200))
        self.assertIn("astronomia", self.labels("faded"))
        later = NOW + timedelta(days=201)
        self.research("astronomia hoje", days_ago=-200)
        self.research("mais astronomia", days_ago=-200)
        self.creation("Nova astronomia", days_ago=-200)
        self.refresh(later + timedelta(days=0))
        self.assertIn("astronomia", self.labels("active"))


class DislikeTests(TasteBase):
    def test_failures_alone_never_become_a_dislike(self):
        for i in range(6):
            self.goal(f"organizar planilha {i}", days_ago=i % 3, status="paused", error="deu erro")
        self.refresh()
        self.assertEqual(self.labels(), {})

    def test_repeated_negative_reactions_form_a_dislike(self):
        self.creation("Poema sobre planilhas", days_ago=2, reaction=-1)
        self.creation("Desenho de planilhas", days_ago=1, reaction=-1)
        self.refresh()
        t = self.labels()["planilhas"]
        self.assertEqual(t["valence"], -1)
        self.assertIn("não curte", tastes.render_block(tastes.active(self.conn)))

    def test_one_negative_reaction_is_not_enough(self):
        self.creation("Poema sobre planilhas", days_ago=1, reaction=-1)
        self.refresh()
        self.assertEqual(self.labels(), {})

    def test_liked_creations_make_a_like(self):
        self.creation("Poema sobre dinossauros", days_ago=2, reaction=1)
        self.creation("Desenho de dinossauros", days_ago=1, reaction=1)
        self.research("dinossauros do jurássico", days_ago=0)
        self.refresh()
        self.assertEqual(self.labels()["dinossauros"]["valence"], 1)


class ControlTests(TasteBase):
    def test_veto_is_permanent_and_restore_recalculates(self):
        self.feed_astronomia()
        self.refresh()
        tid = self.labels()["astronomia"]["id"]
        with transaction(self.conn):
            tastes.set_status(self.conn, tid, "vetoed", NOW)
        self.refresh()
        self.refresh(NOW + timedelta(days=1))
        self.assertNotIn("astronomia", self.labels("active"))
        self.assertIn("astronomia", self.labels("vetoed"))
        self.assertNotIn("astronomia", tastes.render_block(tastes.active(self.conn)))
        with transaction(self.conn):
            tastes.set_status(self.conn, tid, "faded", NOW)
        self.refresh()
        self.assertIn("astronomia", self.labels("active"))

    def test_unknown_taste_and_bad_status(self):
        with self.assertRaises(tastes.TasteError):
            with transaction(self.conn):
                tastes.set_status(self.conn, 999, "vetoed", NOW)
        with self.assertRaises(tastes.TasteError):
            with transaction(self.conn):
                tastes.set_status(self.conn, 1, "active", NOW)

    def test_deleted_source_removes_its_support(self):
        self.feed_astronomia()
        self.refresh()
        with transaction(self.conn):
            self.conn.execute("DELETE FROM creations")
            self.conn.execute("DELETE FROM dream_items")
            self.conn.execute("DELETE FROM goals")
        self.refresh()
        self.assertNotIn("astronomia", self.labels("active"))

    def test_staleness_gate(self):
        self.assertTrue(tastes.is_stale(self.conn, NOW))
        self.refresh()
        self.assertFalse(tastes.is_stale(self.conn, NOW + timedelta(minutes=5)))
        self.assertTrue(tastes.is_stale(self.conn, NOW + timedelta(minutes=30)))


class PriorityTests(TasteBase):
    def setUp(self):
        super().setUp()
        self.feed_astronomia()
        self.creation("Poema sobre planilhas", days_ago=2, reaction=-1)
        self.creation("Desenho de planilhas", days_ago=1, reaction=-1)
        self.refresh()
        self.active = tastes.active(self.conn)

    def test_affinity_has_sign(self):
        self.assertGreater(tastes.affinity("quero ver astronomia", self.active), 0.3)
        self.assertLess(tastes.affinity("organizar planilhas", self.active), 0)
        self.assertEqual(tastes.affinity("cozinhar bolo", self.active), 0)

    def test_priority_follows_taste_within_bounds(self):
        self.assertEqual(tastes.adjust_priority(3, "pesquisar astronomia", self.active)[0], 2)
        self.assertEqual(tastes.adjust_priority(1, "pesquisar astronomia", self.active)[0], 1)
        weak = [dict(t, strength=0.1) for t in self.active]
        self.assertEqual(tastes.adjust_priority(3, "pesquisar astronomia", weak)[0], 3)
        if tastes.affinity("organizar planilhas", self.active) <= -0.3:
            self.assertEqual(tastes.adjust_priority(3, "organizar planilhas", self.active)[0], 4)
            self.assertEqual(tastes.adjust_priority(5, "organizar planilhas", self.active)[0], 5)

    def test_desires_see_the_tastes_and_adjust_priority(self):
        ctx = desires.gather_context(self.conn, iso(30))
        self.assertTrue(ctx["tastes"])
        ctx["existing_goals"] = []
        ctx["memories"] = [{"id": 1, "kind": "semantic", "content": "ele gosta de estrelas"}]
        text = prompt_of(desires.build_messages(ctx, ToolRegistry(), 2))
        self.assertIn("você curte: astronomia", text)
        raw = json.dumps({"goals": [{"description": "Pesquisar novidades de astronomia para contar a ele", "why": "Gosto disso",
                                     "priority": 4, "refs": {"memories": [1]}}]})
        parsed = desires.parse_desires(raw, ctx, 2)
        self.assertEqual(parsed.desires[0].priority, 3)


class ChatTests(TasteBase):
    def test_chat_context_carries_the_tastes_and_the_rule_exists(self):
        self.feed_astronomia()
        provider = FakeProvider()
        ChatService(self.settings, provider, clock=lambda: NOW).send_message("e aí, tudo bem?")
        sent = prompt_of(provider.calls[0])
        self.assertIn("Seus gostos", sent)
        self.assertIn("astronomia", sent)
        system = provider.calls[0][0].content
        self.assertNotIn("astronomia", system)          # nada variável no prompt de sistema (cache do Ollama)
        self.assertTrue(any("gostos e prioridades próprios" in r for r in OPERATIONAL_RULES))

    def test_empty_tastes_tell_him_not_to_invent_favorites(self):
        provider = FakeProvider()
        ChatService(self.settings, provider, clock=lambda: NOW).send_message("do que você gosta?")
        self.assertIn("não invente favoritos", prompt_of(provider.calls[0]))


class ShareTests(PresenceBase):
    def setUp(self):
        super().setUp()
        seed_chat(self.settings, [("user", "falamos sobre coisas", 0), ("assistant", "legal!", 1)])
        self.conn = connect(self.settings.db_path)
        self.addCleanup(self.conn.close)

    def taste(self, origin="own", strength=0.8, valence=1, key="astron"):
        with transaction(self.conn):
            self.conn.execute("INSERT OR REPLACE INTO app_state (key, value, updated_at) VALUES ('tastes_refreshed_at', ?, ?)", (at(170), at(170)))
            tid = self.conn.execute(
                "INSERT INTO tastes (key, label, valence, strength, score, items, days, sources, origin, status, first_seen, last_seen, updated_at) "
                "VALUES (?, 'astronomia', ?, ?, 8, 6, 3, '{\"pesquisa\": 3, \"criacao\": 2}', ?, 'active', ?, ?, ?)",
                (key, valence, strength, origin, at(0), at(0), at(0))).lastrowid
            self.conn.execute("INSERT INTO taste_evidence (taste_id, source, ref, weight, day, snippet) VALUES (?, 'pesquisa', 1, 1, '2026-10-01', 'buracos negros')", (tid,))
        return tid

    def test_he_brings_up_something_he_likes_when_you_are_quiet(self):
        tid = self.taste()
        p = self.build(minutes=3 * 60)
        msg = p.tick()
        self.assertEqual(msg["kind"], "taste_share")
        self.assertIn("astronomia", self.provider.calls[0][-1].content)
        self.assertIn("buracos negros", self.provider.calls[0][-1].content)
        self.assertIsNotNone(rows(self.settings, "SELECT shared_at FROM tastes WHERE id = ?", (tid,))[0]["shared_at"])
        self.assertEqual(self.initiative_count(), 1)

    def test_not_right_after_you_talk(self):
        self.taste()
        self.assertIsNone(self.build(minutes=60).tick())

    def test_only_his_own_strong_likes_are_shared(self):
        self.taste(origin="shared")
        self.assertIsNone(self.build(minutes=3 * 60).tick())
        self.taste(strength=0.1, key="outro")
        self.assertIsNone(self.build(minutes=3 * 60).tick())

    def test_same_taste_has_a_cooldown(self):
        tid = self.taste()
        with transaction(self.conn):
            self.conn.execute("UPDATE tastes SET shared_at = ? WHERE id = ?", (at(3 * 60 - 60), tid))
        self.assertIsNone(self.build(minutes=3 * 60).tick())

    def test_dislikes_are_never_brought_up(self):
        self.taste(valence=-1)
        self.assertIsNone(self.build(minutes=3 * 60).tick())

    def test_a_real_note_comes_before_a_taste(self):
        self.taste()
        self.note()
        msg = self.build(minutes=3 * 60).tick()
        self.assertEqual(msg["kind"], "dream_note")


class MigrationTests(DbTestCase):
    def test_existing_pending_messages_survive_and_new_kind_is_accepted(self):
        conn = connect(self.settings.db_path)
        self.addCleanup(conn.close)
        with transaction(conn):
            conn.execute("INSERT INTO pending_messages (kind, topic, status, created_at) VALUES ('taste_share', 'x', 'pending', ?)", (at(0),))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM pending_messages").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
