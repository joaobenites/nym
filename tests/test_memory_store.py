import unittest
from pathlib import Path

from app.database import repository as repo
from app.database.connection import connect, transaction
from app.memory import store
from app.memory.service import MemoryService
from app.memory.store import InvalidMemoryInput, MemoryNotFound
from tests.fakes import DbTestCase
from tests.memtools import db_dump, seed_chat


class StoreTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = connect(self.settings.db_path)
        self.addCleanup(self.conn.close)

    def make(self, content="O usuário tem um gato chamado Mia", **kw):
        defaults = dict(kind="semantic", epistemic="confirmed", origin="user_stated")
        defaults.update(kw)
        with transaction(self.conn):
            return store.create_memory(self.conn, content=content, **defaults)

    def test_provenance_links_memory_to_real_messages(self):
        ids = seed_chat(self.settings, [("user", "Tenho um gato chamado Mia", 0), ("assistant", "Que fofo!", 1)])
        mid = self.make(message_ids=[ids[0]])
        sources = store.sources_of(self.conn, mid)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["message"]["content"], "Tenho um gato chamado Mia")

    def test_hypothesis_and_confirmed_are_distinct_and_filterable(self):
        fact_id = self.make("O usuário mora em Lisboa")
        hyp_id = self.make("O usuário talvez goste de jazz", epistemic="hypothesis", origin="nym_inferred")
        confirmed = [r["id"] for r in store.list_memories(self.conn, epistemic="confirmed")]
        hypotheses = [r["id"] for r in store.list_memories(self.conn, epistemic="hypothesis")]
        self.assertEqual((confirmed, hypotheses), ([fact_id], [hyp_id]))

    def test_validation(self):
        with self.assertRaises(InvalidMemoryInput):
            self.make(epistemic=None)                      # semântica exige estado epistêmico
        with self.assertRaises(InvalidMemoryInput):
            self.make(epistemic="certeza-absoluta")
        with self.assertRaises(InvalidMemoryInput):
            self.make("   ")
        with self.assertRaises(InvalidMemoryInput):
            self.make("x" * 1001)
        with self.assertRaises(InvalidMemoryInput):
            self.make(importance=1.5)
        epi = self.make("Conversa sobre gatos", kind="episodic", epistemic=None, origin="system")
        self.assertIsNone(store.get_memory(self.conn, epi)["epistemic"])
        with self.assertRaises(InvalidMemoryInput):
            store.update_memory(self.conn, epi, epistemic="confirmed")

    def test_user_edit_keeps_history_and_confirms(self):
        mid = self.make("O usuário mora no Porto", epistemic="hypothesis", origin="nym_inferred")
        with transaction(self.conn):
            row = store.update_memory(self.conn, mid, content="O usuário mora em Braga", actor="user")
        self.assertEqual((row["content"], row["epistemic"], row["origin"]), ("O usuário mora em Braga", "confirmed", "user_edit"))
        edits = [h for h in store.history_of(self.conn, mid) if h["action"] == "edited"]
        self.assertEqual((edits[0]["old_value"], edits[0]["new_value"], edits[0]["actor"]),
                         ("O usuário mora no Porto", "O usuário mora em Braga", "user"))

    def test_unknown_memory(self):
        with self.assertRaises(MemoryNotFound):
            store.require_memory(self.conn, 999)

    def test_archive_and_restore(self):
        mid = self.make()
        with transaction(self.conn):
            store.set_status(self.conn, mid, "archived", "user")
        self.assertEqual(store.list_memories(self.conn), [])
        self.assertEqual(len(store.list_memories(self.conn, status="archived")), 1)
        with transaction(self.conn):
            store.set_status(self.conn, mid, "active", "user")
        self.assertEqual(len(store.list_memories(self.conn)), 1)

    def test_text_search_filter(self):
        self.make("O usuário tem um gato chamado Mia")
        self.make("O usuário trabalha com engenharia")
        found = store.list_memories(self.conn, q="gatos")
        self.assertEqual([r["content"] for r in found], ["O usuário tem um gato chamado Mia"])


class DedupeTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = connect(self.settings.db_path)
        self.addCleanup(self.conn.close)
        self.ids = seed_chat(self.settings, [("user", "a", 0), ("user", "b", 1), ("user", "c", 2)])

    def make(self, content, msg, **kw):
        with transaction(self.conn):
            return store.create_memory(self.conn, kind="semantic", content=content, epistemic="confirmed",
                                       origin="user_stated", message_ids=[msg], **kw)

    def test_dry_run_changes_nothing(self):
        a, b = self.make("O usuário tem um gato chamado Mia", self.ids[0]), self.make("O usuário tem um gato, chamado Mia.", self.ids[1])
        merges = store.dedupe_batch(self.conn, dry_run=True)
        self.assertEqual(merges, [{"keep": a, "drop": b}])
        self.assertEqual(len(store.list_memories(self.conn)), 2)

    def test_merge_keeps_traceability(self):
        a = self.make("O usuário tem um gato chamado Mia", self.ids[0])
        b = self.make("O usuário tem um gato, chamado Mia.", self.ids[1])
        c = self.make("O usuário trabalha com engenharia", self.ids[2])
        with transaction(self.conn):
            store.dedupe_batch(self.conn, dry_run=False)
        active = {r["id"] for r in store.list_memories(self.conn)}
        self.assertEqual(active, {a, c})
        kept = store.get_memory(self.conn, a)
        self.assertEqual(kept["recurrence"], 2)
        self.assertEqual({s["message_id"] for s in store.sources_of(self.conn, a)}, {self.ids[0], self.ids[1]})
        dropped = store.get_memory(self.conn, b)
        self.assertEqual((dropped["status"], dropped["superseded_by"]), ("superseded", a))
        self.assertIn({"from_id": b, "to_id": a, "relation": "duplicate_of"}, store.links_of(self.conn, a))
        merged = [h for h in store.history_of(self.conn, a) if h["action"] == "merged"]
        self.assertEqual(len(merged), 1)
        self.assertNotIn("gato", (merged[0]["old_value"] or "") + (merged[0]["new_value"] or ""))  # sem conteúdo alheio

    def test_merge_adopts_more_complete_text(self):
        a = self.make("O usuário gosta de café preto sem açúcar", self.ids[0])
        self.make("O usuário gosta de café preto sem açúcar e de pão", self.ids[1])
        with transaction(self.conn):
            store.dedupe_batch(self.conn, dry_run=False)
        self.assertEqual(store.get_memory(self.conn, a)["content"], "O usuário gosta de café preto sem açúcar e de pão")

    def test_contradictory_memories_are_not_merged(self):
        self.make("O usuário gosta de café", self.ids[0])
        self.make("O usuário não gosta de café", self.ids[1])
        self.assertEqual(store.dedupe_batch(self.conn, dry_run=True), [])


class DeletionPolicyTests(DbTestCase):
    SENTINEL = "zebra-quantica-731"

    def setUp(self):
        super().setUp()
        self.svc = MemoryService(self.settings)
        self.conn = connect(self.settings.db_path)
        self.addCleanup(self.conn.close)
        self.ids = seed_chat(self.settings, [
            ("user", f"Adoro a {self.SENTINEL}, é meu segredo", 0),
            ("assistant", "Interessante", 1),
            ("user", "Também gosto de cachorros", 2),
        ])
        with transaction(self.conn):
            self.episode = store.create_memory(self.conn, kind="episodic", content=f"O usuário falou da {self.SENTINEL}",
                                               epistemic=None, origin="system", message_ids=self.ids)
            self.fact = store.create_memory(self.conn, kind="semantic", content=f"O usuário adora a {self.SENTINEL}",
                                            epistemic="confirmed", origin="user_stated", message_ids=[self.ids[0]])
            self.other = store.create_memory(self.conn, kind="semantic", content="O usuário gosta de cachorros",
                                             epistemic="confirmed", origin="user_stated", message_ids=[self.ids[2]])
            store.add_link(self.conn, self.fact, self.episode, "part_of")
            store.update_memory(self.conn, self.fact, content=f"O usuário adora muito a {self.SENTINEL}", actor="user")

    def test_default_delete_removes_record_history_and_sources_but_keeps_messages(self):
        self.svc.delete(self.fact)
        with self.assertRaises(MemoryNotFound):
            self.svc.get(self.fact)
        dump = db_dump(self.settings)
        self.assertNotIn("adora muito", dump)      # nem o histórico de edições sobrevive
        self.assertNotIn("O usuário adora a", dump)
        self.assertIn(self.SENTINEL, dump)         # o episódio e a mensagem ainda existem (política documentada)
        self.assertEqual(len(self.svc.get(self.episode)["sources"]), 3)

    def test_delete_leaves_only_a_tombstone_without_content(self):
        self.svc.delete(self.fact)
        conn = connect(self.settings.db_path)
        rows = conn.execute("SELECT event, detail FROM audit_log WHERE event = 'memory.deleted'").fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertNotIn(self.SENTINEL, rows[0]["detail"])
        self.assertNotIn("adora", rows[0]["detail"])

    def test_purge_sources_erases_everything_derived_from_those_messages(self):
        result = self.svc.delete(self.fact, purge_sources=True)
        self.assertIn(self.episode, result["memories_deleted"])   # o resumo também citava a mensagem
        self.assertEqual(result["messages_deleted"], 1)
        self.assertNotIn(self.SENTINEL, db_dump(self.settings))
        self.assertEqual(self.svc.get(self.other)["content"], "O usuário gosta de cachorros")  # o resto fica

    def test_secure_delete_scrubs_the_database_file(self):
        self.svc.delete(self.fact, purge_sources=True)
        conn = connect(self.settings.db_path)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        for name in ("nym.db", "nym.db-wal"):
            path = Path(self.settings.data_dir) / name
            if path.exists():
                self.assertNotIn(self.SENTINEL.encode(), path.read_bytes(), name)

    def test_deleting_the_replacement_reactivates_nothing_silently(self):
        with transaction(self.conn):
            new = store.create_memory(self.conn, kind="semantic", content="O usuário adora a lontra", epistemic="confirmed", origin="user_stated")
            store.supersede(self.conn, self.other, new, "system", "teste")
        self.svc.delete(new)
        self.assertEqual(self.svc.get(self.other)["status"], "archived")  # volta como arquivada, sem referência pendente


class UserToolsTests(DbTestCase):
    def test_create_edit_and_duplicate_guard(self):
        svc = MemoryService(self.settings)
        created = svc.create("O usuário nasceu em março")
        self.assertEqual((created["origin"], created["epistemic"]), ("user_edit", "confirmed"))
        self.assertEqual(svc.get(created["id"])["sources"][0]["source_type"], "manual")
        with self.assertRaises(InvalidMemoryInput):
            svc.create("O usuário nasceu em março.")
        with self.assertRaises(InvalidMemoryInput):
            svc.create("x", kind="episodic")
        updated = svc.update(created["id"], epistemic="unverified")
        self.assertEqual(updated["epistemic"], "unverified")


class IdentityCandidatesTests(DbTestCase):
    def test_single_observation_never_qualifies_but_recurrence_across_episodes_does(self):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            eps = [store.create_memory(conn, kind="episodic", content=f"episódio {i}", epistemic=None, origin="system") for i in range(3)]
            note = store.create_memory(conn, kind="identity", content="O usuário prefere respostas curtas",
                                       epistemic="hypothesis", origin="nym_inferred")
            store.add_link(conn, note, eps[0], "part_of")
        self.assertEqual(store.recurring_identity_candidates(conn), [])
        with transaction(conn):
            for ep in eps[1:]:
                store.bump_recurrence(conn, note, confidence=0.6, message_ids=[])
                store.add_link(conn, note, ep, "part_of")
        candidates = store.recurring_identity_candidates(conn)
        self.assertEqual([(c["id"], c["recurrence"], c["episodes"]) for c in candidates], [(note, 3, 3)])
        conn.close()


if __name__ == "__main__":
    unittest.main()
