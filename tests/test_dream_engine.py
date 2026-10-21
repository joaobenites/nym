import json
import unittest
from datetime import timedelta
from unittest import mock

from app.core.identity import IdentityService
from app.database.connection import connect, transaction
from app.dreams.engine import DreamEngine, select_memories
from app.dreams.journal import DreamJournal
from app.memory import store
from app.memory.service import MemoryService
from app.models.base import ModelUnavailableError
from tests.dreamtools import add_mem, dream_reply, rows
from tests.fakes import DbTestCase, ScriptedProvider
from tests.memtools import BASE, at, clock_at


def make_engine(settings, replies, clock=None, provider_cls=ScriptedProvider):
    provider = provider_cls(replies)
    engine = DreamEngine(settings, provider, IdentityService(settings), clock=clock or clock_at(0))
    return engine, provider


class SelectionTests(DbTestCase):
    def test_new_memories_first_then_important_old_ones(self):
        old_important = add_mem(self.settings, "O usuário é pai de duas crianças", importance=0.9, recurrence=4, minutes=-1000)
        old_trivial = add_mem(self.settings, "O usuário comeu pão hoje", importance=0.1, minutes=-1000)
        conn = connect(self.settings.db_path)
        with transaction(conn):
            conn.execute("INSERT INTO dream_cycles (trigger, status, started_at) VALUES ('manual', 'completed', ?)", (at(-500),))
        new = add_mem(self.settings, "O usuário começou um curso novo", importance=0.6, minutes=10)
        got, new_count = select_memories(conn, 3)
        conn.close()
        self.assertEqual(new_count, 1)
        self.assertEqual([m["id"] for m in got], sorted([new, old_important, old_trivial]))
        conn = connect(self.settings.db_path)
        got2, _ = select_memories(conn, 2)
        conn.close()
        self.assertEqual({m["id"] for m in got2}, {new, old_important})   # o trivial fica de fora quando há limite


class CycleTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.a = add_mem(self.settings, "O usuário tem um gato chamado Mia", importance=0.8)
        self.b = add_mem(self.settings, "O usuário quer adotar outro gato em breve", importance=0.6)
        self.c = add_mem(self.settings, "O usuário trabalha muito à noite", importance=0.5)
        self.full = dream_reply(
            review="O usuário fala muito de gatos e de trabalho à noite.",
            associations=[{"memories": [self.a, self.b], "text": "Ambas tratam de gatos", "confidence": 0.8}],
            hypotheses=[{"text": "Talvez o usuário se sinta sozinho à noite", "evidence": [self.c], "confidence": 0.9}],
            questions=[{"text": "Quando o usuário pretende adotar o novo gato?", "evidence": [self.b]}],
            exploration=[{"question": "Como apresentar um gato novo ao gato antigo?", "why": "ele quer adotar outro", "evidence": [self.a, self.b]}],
            note={"text": "Você chegou a decidir sobre o gato novo?", "evidence": [self.b]})

    def run_cycle(self, reply=None, **kw):
        engine, provider = make_engine(self.settings, [reply or self.full], **kw)
        return engine.run_cycle("manual"), provider

    def test_complete_cycle_writes_everything_with_lastro(self):
        result, provider = self.run_cycle()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(provider.kwargs[0], {"temperature": 0.4, "json_mode": True})

        cycle = DreamJournal(self.settings).get_cycle(result["cycle_id"])
        self.assertEqual(cycle["status"], "completed")
        self.assertEqual(cycle["model_calls"], 1)
        self.assertEqual(set(cycle["selected_ids"]), {self.a, self.b, self.c})
        self.assertIn("gatos", cycle["review"])
        names = [s["stage"] for s in cycle["stage_log"]]
        self.assertEqual(names, ["selecao", "revisao", "associacao", "consolidacao", "exploracao", "reflexao_identidade", "registro"])
        kinds = {i["kind"] for i in cycle["items"]}
        self.assertEqual(kinds, {"association", "hypothesis", "open_question", "dream_note"})

        links = rows(self.settings, "SELECT from_id, to_id, relation FROM memory_links WHERE relation = 'related'")
        self.assertEqual([(l["from_id"], l["to_id"]) for l in links], [(self.a, self.b)])

        hyp = [m for m in rows(self.settings, "SELECT * FROM memories WHERE epistemic = 'hypothesis'")][0]
        self.assertEqual((hyp["origin"], hyp["importance"]), ("nym_inferred", 0.4))
        self.assertLessEqual(hyp["confidence"], 0.6)
        supports = rows(self.settings, "SELECT from_id FROM memory_links WHERE relation = 'supports' AND to_id = ?", (hyp["id"],))
        self.assertEqual([s["from_id"] for s in supports], [self.c])         # a hipótese cita a memória que a sustenta

        qs = DreamJournal(self.settings).open_questions()
        self.assertEqual(len(qs), 1)
        self.assertEqual(len(DreamJournal(self.settings).explorations("pending")), 1)
        notes = rows(self.settings, "SELECT * FROM pending_messages WHERE status = 'pending'")
        self.assertEqual([n["kind"] for n in notes], ["dream_note"])

    def test_original_memories_are_never_rewritten(self):
        before = {r["id"]: r["content"] for r in rows(self.settings, "SELECT id, content FROM memories")}
        self.run_cycle()
        after = {r["id"]: r["content"] for r in rows(self.settings, "SELECT id, content FROM memories")}
        self.assertTrue(all(after[i] == c for i, c in before.items()))

    def test_second_cycle_does_not_repeat_questions_or_hypotheses(self):
        self.run_cycle()
        engine, _ = make_engine(self.settings, [self.full])
        engine.run_cycle("manual")
        self.assertEqual(len(DreamJournal(self.settings).open_questions()), 1)
        self.assertEqual(len(DreamJournal(self.settings).explorations("pending")), 1)
        self.assertEqual(len(rows(self.settings, "SELECT 1 FROM memories WHERE epistemic = 'hypothesis'")), 1)

    def test_exploration_limits(self):
        many = dream_reply(exploration=[{"question": f"Pergunta totalmente distinta número {i} sobre o tema {i}", "evidence": [self.a]} for i in range(3)])
        # o parser já limita a 3 por ciclo; o acumulado pendente também é limitado
        for _ in range(5):
            engine, _ = make_engine(self.settings, [dream_reply(exploration=[
                {"question": f"Investigar assunto único {n} {k}", "evidence": [self.a]} for n in range(3) for k in [_]])])
            engine.run_cycle("manual")
        self.assertLessEqual(len(DreamJournal(self.settings).explorations("pending")), 10)

    def test_idle_trigger_skips_when_nothing_is_new(self):
        self.run_cycle()
        engine, provider = make_engine(self.settings, [self.full], clock=clock_at(10))
        result = engine.run_cycle("idle")
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(provider.calls, [])        # nem chamou o modelo

    def test_no_memories_skips(self):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            conn.execute("DELETE FROM memories")
        conn.close()
        self.assertEqual(self.run_cycle()[0]["status"], "skipped")

    # --- falhas e interrupções: nada de corromper ------------------------------------------------------
    def test_model_unavailable_fails_cleanly(self):
        result, _ = self.run_cycle(ModelUnavailableError("fora do ar"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(rows(self.settings, "SELECT COUNT(*) AS n FROM dream_items")[0]["n"], 0)
        self.assertEqual(len(rows(self.settings, "SELECT 1 FROM memories")), 3)

    def test_invalid_output_fails_cleanly(self):
        result, _ = self.run_cycle("não é json")
        self.assertEqual(result["status"], "failed")
        self.assertIn("JSON", result["error"])

    def test_failure_in_the_middle_of_writing_rolls_everything_back(self):
        with mock.patch("app.dreams.engine.store.create_memory", side_effect=RuntimeError("disco cheio")):
            result, _ = self.run_cycle()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(rows(self.settings, "SELECT COUNT(*) AS n FROM dream_items")[0]["n"], 0)       # nem a associação anterior
        self.assertEqual(rows(self.settings, "SELECT COUNT(*) AS n FROM memory_links WHERE relation = 'related'")[0]["n"], 0)
        cycle = DreamJournal(self.settings).get_cycle(result["cycle_id"])
        done = [s["stage"] for s in cycle["stage_log"]]
        self.assertNotIn("associacao", done)         # o diário não finge que etapas desfeitas aconteceram
        self.assertIn("falha", done)
        self.assertEqual(len(rows(self.settings, "SELECT 1 FROM memories")), 3)

    def test_cancel_during_the_model_call_interrupts_without_touching_memories(self):
        holder = {}

        class CancelsItself(ScriptedProvider):
            def chat(self, messages, **kw):
                holder["engine"].cancel()
                return super().chat(messages, **kw)

        engine, _ = make_engine(self.settings, [self.full], provider_cls=CancelsItself)
        holder["engine"] = engine
        result = engine.run_cycle("manual")
        self.assertEqual(result["status"], "interrupted")
        self.assertIn("cancelado", result["error"])
        self.assertEqual(rows(self.settings, "SELECT COUNT(*) AS n FROM dream_items")[0]["n"], 0)
        self.assertEqual(len(rows(self.settings, "SELECT 1 FROM memories")), 3)
        self.assertFalse(engine.is_running())
        again, _ = self.run_cycle()                   # e o próximo ciclo funciona normalmente
        self.assertEqual(again["status"], "completed")

    def test_duration_limit_interrupts(self):
        ticks = {"n": 0}

        def clock():
            ticks["n"] += 1
            return BASE + timedelta(seconds=ticks["n"] * 400)   # cada leitura do relógio avança 400 s (limite: 300)

        result, _ = self.run_cycle(clock=clock)
        self.assertEqual(result["status"], "interrupted")
        self.assertIn("duração", result["error"])
        self.assertEqual(len(rows(self.settings, "SELECT 1 FROM memories")), 3)

    def test_only_one_cycle_at_a_time(self):
        engine, _ = make_engine(self.settings, [self.full])
        self.assertTrue(engine._lock.acquire(blocking=False))
        try:
            self.assertEqual(engine.run_cycle("manual")["status"], "busy")
            self.assertTrue(engine.cancel())
        finally:
            engine._lock.release()
        self.assertFalse(engine.cancel())

    def test_stale_running_cycles_are_recovered_after_a_crash(self):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            conn.execute("INSERT INTO dream_cycles (trigger, status, started_at) VALUES ('idle', 'running', ?)", (at(0),))
        conn.close()
        self.assertEqual(DreamJournal(self.settings).recover_stale(), 1)
        self.assertEqual(DreamJournal(self.settings).list_cycles()[0]["status"], "interrupted")

    def test_model_call_limit_zero_is_respected(self):
        engine, provider = make_engine(self.make_settings(dream_max_model_calls=1), [self.full])
        engine._settings = self.make_settings(dream_max_model_calls=0)
        self.assertEqual(engine.run_cycle("manual")["status"], "failed")
        self.assertEqual(provider.calls, [])


class ConsolidationInDreamsTests(DbTestCase):
    def test_duplicates_are_merged_and_stale_episodes_archived_reversibly(self):
        a = add_mem(self.settings, "O usuário gosta de café preto sem açúcar")
        b = add_mem(self.settings, "O usuário gosta de café preto, sem açúcar.")
        old_low = add_mem(self.settings, "Conversa curta sobre o clima", kind="episodic", importance=0.1, minutes=-60 * 24 * 90)
        old_high = add_mem(self.settings, "Conversa importante sobre a família", kind="episodic", importance=0.9, minutes=-60 * 24 * 90)
        old_used = add_mem(self.settings, "Conversa lembrada várias vezes", kind="episodic", importance=0.1, minutes=-60 * 24 * 90, access_count=3)
        engine, _ = make_engine(self.settings, [dream_reply()], clock=clock_at(0))
        result = engine.run_cycle("manual")
        self.assertEqual(result["status"], "completed")
        status = {r["id"]: r["status"] for r in rows(self.settings, "SELECT id, status FROM memories")}
        self.assertEqual((status[a], status[b]), ("active", "superseded"))        # fundidas, com rastro
        self.assertEqual((status[old_low], status[old_high], status[old_used]), ("archived", "active", "active"))
        kinds = {i["kind"] for i in DreamJournal(self.settings).get_cycle(result["cycle_id"])["items"]}
        self.assertEqual(kinds, {"consolidation", "archive"})
        MemoryService(self.settings).restore(old_low)                            # arquivar é reversível
        self.assertEqual(MemoryService(self.settings).get(old_low)["status"], "active")


class IdentityReflectionTests(DbTestCase):
    def setUp(self):
        super().setUp()
        conn = connect(self.settings.db_path)
        with transaction(conn):
            eps = [store.create_memory(conn, kind="episodic", content=f"episódio {i}", epistemic=None, origin="system") for i in range(3)]
            self.note = store.create_memory(conn, kind="identity", content="O usuário prefere respostas curtas",
                                            epistemic="hypothesis", origin="nym_inferred", confidence=0.6)
            store.add_link(conn, self.note, eps[0], "part_of")
        conn.close()

    def cycle(self):
        engine, _ = make_engine(self.settings, [dream_reply()])
        return engine.run_cycle("manual")

    def test_single_observation_does_not_propose_anything(self):
        self.cycle()
        self.assertEqual(IdentityService(self.settings).list_changes("proposed"), [])

    def test_recurring_observation_becomes_a_pending_proposal_never_a_direct_change(self):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            eps = [r["id"] for r in conn.execute("SELECT id FROM memories WHERE kind = 'episodic' ORDER BY id")]
            for ep in eps[1:]:
                store.bump_recurrence(conn, self.note, confidence=0.6, message_ids=[])
                store.add_link(conn, self.note, ep, "part_of")
        conn.close()
        result = self.cycle()
        pending = IdentityService(self.settings).list_changes("proposed")
        self.assertEqual(len(pending), 1)
        self.assertEqual((pending[0]["origin"], pending[0]["kind"], pending[0]["related_memory_ids"]), ("nym", "preference", [self.note]))
        self.assertEqual([e["value"] for e in IdentityService(self.settings).snapshot()["entries"] if e["kind"] == "preference"], [])
        cycle = DreamJournal(self.settings).get_cycle(result["cycle_id"])
        self.assertIn("identity_proposal", {i["kind"] for i in cycle["items"]})
        self.cycle()                                                              # outro sonho não repete a proposta
        self.assertEqual(len(IdentityService(self.settings).list_changes("proposed")), 1)


class RetentionTests(DbTestCase):
    def test_deleting_a_memory_erases_its_traces_in_the_dreams(self):
        secret = add_mem(self.settings, "O usuário coleciona selos da zebra-quantica-731")
        other = add_mem(self.settings, "O usuário gosta de ler romances")
        reply = dream_reply(
            review="Revisão que menciona a zebra-quantica-731.",
            associations=[{"memories": [secret, other], "text": "Ambos são hobbies, incluindo a zebra-quantica-731"}],
            hypotheses=[{"text": "Talvez a zebra-quantica-731 seja um hobby de longa data", "evidence": [secret]}],
            questions=[{"text": "Desde quando a zebra-quantica-731 é um hobby?", "evidence": [secret]}],
            exploration=[{"question": "Como catalogar a zebra-quantica-731?", "evidence": [secret]}],
            note={"text": "E a zebra-quantica-731, como vai?", "evidence": [secret]})
        engine, _ = make_engine(self.settings, [reply])
        engine.run_cycle("manual")
        conn = connect(self.settings.db_path)
        dump_before = "\n".join(conn.iterdump())
        conn.close()
        self.assertIn("zebra-quantica-731", dump_before)

        hyp = rows(self.settings, "SELECT id FROM memories WHERE epistemic = 'hypothesis'")[0]["id"]
        MemoryService(self.settings).delete(secret)
        MemoryService(self.settings).delete(hyp)
        conn = connect(self.settings.db_path)
        dump = "\n".join(conn.iterdump())
        conn.close()
        self.assertNotIn("zebra-quantica-731", dump)
        self.assertEqual(rows(self.settings, "SELECT COUNT(*) AS n FROM pending_messages")[0]["n"], 0)


if __name__ == "__main__":
    unittest.main()
