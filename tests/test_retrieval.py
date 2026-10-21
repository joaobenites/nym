import unittest
from datetime import datetime, timedelta, timezone

from app.database.connection import connect, transaction
from app.memory import store
from app.memory.retrieval import RetrievalWeights, format_block, retrieve
from tests.fakes import DbTestCase
from tests.memtools import BASE, at, seed_chat

NOW = BASE + timedelta(days=1)


class RetrievalTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = connect(self.settings.db_path)
        self.addCleanup(self.conn.close)

    def sem(self, content, *, epistemic="confirmed", origin="user_stated", importance=0.5, confidence=0.8,
            minutes=0, message_ids=()):
        with transaction(self.conn):
            mid = store.create_memory(self.conn, kind="semantic", content=content, epistemic=epistemic, origin=origin,
                                      importance=importance, confidence=confidence, message_ids=message_ids)
            self.conn.execute("UPDATE memories SET updated_at = ?, occurred_at = ? WHERE id = ?", (at(minutes), at(minutes), mid))
        return mid

    def ids(self, query, **kw):
        return [h.memory["id"] for h in retrieve(self.conn, query, now=NOW, **kw)]

    def test_relevant_memory_is_found_and_irrelevant_is_not(self):
        gato = self.sem("O usuário tem um gato chamado Mia")
        self.sem("O usuário trabalha com engenharia civil")
        self.sem("O usuário gosta de jogar xadrez aos domingos")
        self.assertEqual(self.ids("como está a Mia, aquele gato?"), [gato])
        self.assertEqual(self.ids("qual a capital da Mongólia?"), [])  # nada relevante: nada entra

    def test_precision_at_one_on_a_small_fixture(self):
        facts = {
            "gato": self.sem("O usuário tem um gato chamado Mia"),
            "trabalho": self.sem("O usuário trabalha como engenheiro civil em obras de pontes"),
            "xadrez": self.sem("O usuário joga xadrez aos domingos com o irmão"),
            "cidade": self.sem("O usuário mora em Lisboa desde 2020"),
            "cafe": self.sem("O usuário bebe café preto sem açúcar de manhã"),
        }
        queries = {
            "gato": "meu gato Mia está doente",
            "trabalho": "estou cansado das obras e das pontes no trabalho",
            "xadrez": "quer jogar xadrez no domingo?",
            "cidade": "o que fazer em Lisboa neste fim de semana",
            "cafe": "preciso de um café preto agora",
        }
        hits = {name: self.ids(q)[:1] for name, q in queries.items()}
        correct = sum(1 for name, top in hits.items() if top == [facts[name]])
        self.assertEqual(correct, len(queries), hits)  # precisão@1 = 100% neste conjunto

    def test_recency_breaks_ties(self):
        a = self.sem("O usuário pratica natação toda semana", minutes=0)
        b = self.sem("O usuário pratica natação toda semana de manhã", minutes=60 * 20)
        order = self.ids("natação toda semana")
        self.assertLess(order.index(b), order.index(a))

    def test_importance_and_weights_are_configurable(self):
        low = self.sem("O usuário gosta de ler romances policiais", importance=0.1, minutes=60 * 20)
        high = self.sem("O usuário gosta de ler romances históricos", importance=0.95, minutes=0)
        default = self.ids("gosta de ler romances")
        self.assertEqual(default[0], high)  # importância pesa mais que um pouco de recência
        recency_only = RetrievalWeights(lexical=0.0, importance=0.0, recurrence=0.0, confidence=0.0,
                                        goal=0.0, relation=0.0, recency=1.0, min_lexical=0.1)
        flipped = self.ids("gosta de ler romances", weights=recency_only)
        self.assertEqual(flipped[0], low)

    def test_exclude_memories_already_in_context(self):
        ids = seed_chat(self.settings, [("user", "Tenho um gato chamado Mia", 0)])
        mia = self.sem("O usuário tem um gato chamado Mia", message_ids=ids)
        self.assertEqual(self.ids("o gato Mia"), [mia])
        self.assertEqual(self.ids("o gato Mia", exclude_message_ids=set(ids)), [])

    def test_archived_and_superseded_are_never_returned(self):
        a = self.sem("O usuário mora no Porto")
        b = self.sem("O usuário mora em Braga")
        with transaction(self.conn):
            store.supersede(self.conn, a, b, "system", "mudou")
        self.assertEqual(self.ids("onde o usuário mora"), [b])
        with transaction(self.conn):
            store.set_status(self.conn, b, "archived", "user")
        self.assertEqual(self.ids("onde o usuário mora"), [])

    def test_core_facts_always_included_but_limited(self):
        name = self.sem("O usuário se chama Gui", importance=0.95)
        for i in range(5):
            self.sem(f"Fato central número{i} muito importante {i}", importance=0.9)
        got = self.ids("assunto totalmente diferente sobre foguetes")
        self.assertIn(name, got)
        self.assertLessEqual(len(got), RetrievalWeights().core_slots)

    def test_relation_expansion_brings_the_episode_of_a_fact(self):
        with transaction(self.conn):
            ep = store.create_memory(self.conn, kind="episodic", content="Conversa sobre a viagem à Islândia em dezembro",
                                     epistemic=None, origin="system", importance=0.6)
        fact = self.sem("O usuário quer ver a aurora boreal")
        with transaction(self.conn):
            store.add_link(self.conn, fact, ep, "part_of")
        hits = retrieve(self.conn, "aurora boreal", now=NOW)
        by_id = {h.memory["id"]: h for h in hits}
        self.assertEqual(by_id[fact].why, "relevante")
        self.assertEqual(by_id[ep].why, "relacionada")

    def test_components_are_exposed_and_bounded(self):
        self.sem("O usuário tem um gato chamado Mia", importance=0.7)
        hit = retrieve(self.conn, "gato Mia", now=NOW)[0]
        self.assertEqual(set(hit.components), {"lexical", "recency", "importance", "recurrence", "confidence", "goal", "relation"})
        self.assertTrue(all(0 <= v <= 1 for v in hit.components.values()))

    def test_goal_relevance_raises_score(self):
        a = self.sem("O usuário estuda alemão para uma prova")
        b = self.sem("O usuário estuda francês para uma viagem")
        before = {h.memory["id"]: h.score for h in retrieve(self.conn, "estuda para", now=NOW)}
        with transaction(self.conn):
            self.conn.execute("INSERT INTO goals (description, origin, priority, status, created_at, updated_at) "
                              "VALUES ('passar na prova de alemão', 'user', 2, 'active', 'x', 'x')")
        after = {h.memory["id"]: h.score for h in retrieve(self.conn, "estuda para", now=NOW)}
        self.assertGreater(after[a] - before[a], after[b] - before[b])

    def test_conflict_is_labeled_in_the_prompt_block(self):
        a = self.sem("O usuário gosta de café", epistemic="confirmed")
        b = self.sem("O usuário não gosta de café", epistemic="hypothesis", origin="nym_inferred")
        with transaction(self.conn):
            store.add_link(self.conn, b, a, "contradicts")
        block, used = format_block(retrieve(self.conn, "gosta de café", now=NOW), 2000)
        self.assertEqual(set(used), {a, b})
        self.assertIn("em conflito", block)
        self.assertIn("[hipótese", block)
        self.assertIn("[confirmado", block)

    def test_block_respects_budget_and_reports_only_included_ids(self):
        for i in range(10):
            self.sem(f"O usuário coleciona selos raros da região número {i} " + "x" * 60)
        hits = retrieve(self.conn, "coleciona selos raros", now=NOW, limit=10)
        block, used = format_block(hits, 300)
        self.assertLessEqual(len(block) - len(block.split("\n")[0]), 300 + len(used))
        self.assertTrue(0 < len(used) < 10)
        self.assertEqual(format_block(hits, 0), ("", []))


if __name__ == "__main__":
    unittest.main()
