import json
import unittest
from datetime import timedelta
from unittest import mock

from app.core.activity import ActivityGate
from app.core.chat_service import ChatService
from app.database import repository as repo
from app.database.connection import connect
from app.memory import store
from app.memory.consolidation import Consolidator, MemoryWorker
from app.memory.service import MemoryService
from app.models.base import ModelError, ModelUnavailableError
from tests.fakes import prompt_of, spoken, DbTestCase, FakeProvider, ScriptedProvider
from tests.memtools import clock_at, extraction, fact, seed_chat

LATER = clock_at(60 * 24)  # um dia depois: todo trecho já está fechado


def memories(settings, **kw):
    return MemoryService(settings).list(status=None, **kw)


def jobs(settings):
    return MemoryService(settings).jobs()


class PlanningTests(DbTestCase):
    def consolidator(self, clock, replies=()):
        return Consolidator(self.settings, ScriptedProvider(replies), clock=clock)

    def test_gap_splits_episodes_and_open_segment_waits(self):
        seed_chat(self.settings, [
            ("user", "Tenho um gato chamado Mia e adoro ela muito", 0), ("assistant", "que fofo", 1),
            ("user", "Hoje fui ao mercado comprar pão e leite frescos", 120), ("assistant", "boa", 121),
        ])
        c = self.consolidator(clock_at(125))           # o último trecho ainda está acontecendo
        report = c.run_once(max_jobs=0)
        self.assertEqual(report["planned"], 1)
        self.assertEqual(len(jobs(self.settings)), 1)
        report = c.run_once(max_jobs=0)                # replanejar não duplica
        self.assertEqual(report["planned"], 0)
        later = self.consolidator(clock_at(400))       # agora o segundo trecho também fechou
        self.assertEqual(later.run_once(max_jobs=0)["planned"], 1)
        self.assertEqual(len(jobs(self.settings)), 2)

    def test_force_closes_the_open_segment(self):
        seed_chat(self.settings, [("user", "Acabei de adotar um cachorro chamado Rex hoje", 0)])
        c = self.consolidator(clock_at(1))
        self.assertEqual(c.run_once(max_jobs=0)["planned"], 0)
        self.assertEqual(c.run_once(force=True, max_jobs=0)["planned"], 1)

    def test_trivial_segments_are_skipped(self):
        seed_chat(self.settings, [("user", "oi", 0), ("assistant", "e aí!", 1)])
        self.consolidator(LATER).run_once(max_jobs=0)
        self.assertEqual([j["status"] for j in jobs(self.settings)], ["skipped"])

    def test_size_cap_splits_long_runs(self):
        items = []
        for i in range(70):
            items.append(("user", f"mensagem longa número {i} sobre assuntos variados do dia a dia", i))
        seed_chat(self.settings, items)
        self.consolidator(LATER).run_once(max_jobs=0)
        self.assertGreaterEqual(len(jobs(self.settings)), 3)  # 70 mensagens / 30 por trecho


class ProcessingTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.ids = seed_chat(self.settings, [
            ("user", "Tenho um gato chamado Mia e adoro ela, ela dorme no meu colo", 0),
            ("assistant", "Que fofo, a Mia parece ótima!", 1),
            ("user", "Prefiro respostas curtas, não gosto de textão", 2),
        ])

    def run_with(self, *replies, clock=LATER, **kw):
        provider = ScriptedProvider(replies)
        report = Consolidator(self.settings, provider, clock=clock).run_once(**kw)
        return report, provider

    def test_creates_episode_facts_sources_and_links(self):
        reply = extraction(
            summary="O usuário falou do gato Mia.", outcome="nada pendente",
            facts=[fact("O usuário tem um gato chamado Mia", [self.ids[0]])],
            notes=[{"text": "O usuário prefere respostas curtas", "evidence": [self.ids[2]], "confidence": 0.6}],
        )
        report, provider = self.run_with(reply)
        self.assertEqual(report["processed"][0]["status"], "done")
        self.assertEqual(provider.kwargs[0], {"temperature": 0.2, "json_mode": True})

        episode = memories(self.settings, kind="episodic")[0]
        self.assertEqual(episode["content"], "O usuário falou do gato Mia.")
        svc = MemoryService(self.settings)
        detail = svc.get(episode["id"])
        self.assertEqual({s["message_id"] for s in detail["sources"]}, set(self.ids))  # fonte = o trecho inteiro

        f = memories(self.settings, kind="semantic")[0]
        self.assertEqual((f["epistemic"], f["origin"]), ("confirmed", "user_stated"))
        fdetail = svc.get(f["id"])
        self.assertEqual([s["message_id"] for s in fdetail["sources"]], [self.ids[0]])
        self.assertIn({"from_id": f["id"], "to_id": episode["id"], "relation": "part_of"}, fdetail["links"])

        note = memories(self.settings, kind="identity")[0]
        self.assertEqual((note["epistemic"], note["origin"]), ("hypothesis", "nym_inferred"))
        self.assertEqual(jobs(self.settings)[0]["status"], "done")

    def test_inferred_fact_is_stored_as_hypothesis_not_fact(self):
        reply = extraction(facts=[fact("O usuário provavelmente mora em apartamento", [self.ids[0]], kind="inferred")])
        self.run_with(reply)
        f = memories(self.settings, kind="semantic")[0]
        self.assertEqual((f["epistemic"], f["origin"]), ("hypothesis", "nym_inferred"))

    def test_model_unavailable_keeps_job_pending_then_recovers(self):
        report, _ = self.run_with(ModelUnavailableError("ollama fora do ar"))
        self.assertEqual(report["processed"][0]["status"], "unavailable")
        job = jobs(self.settings)[0]
        self.assertEqual((job["status"], job["attempts"]), ("pending", 0))   # indisponibilidade não gasta tentativa
        self.assertEqual(memories(self.settings), [])
        report, _ = self.run_with(extraction(facts=[fact("O usuário tem um gato chamado Mia", [self.ids[0]])]))
        self.assertEqual(report["processed"][0]["status"], "done")
        self.assertEqual(len(memories(self.settings, kind="semantic")), 1)

    def test_invalid_output_is_retried_then_marked_failed(self):
        for expected in ("pending", "pending", "failed"):
            report, _ = self.run_with("isso não é json", force=True)
            self.assertEqual(report["processed"][0]["status"], expected)
        job = jobs(self.settings)[0]
        self.assertEqual((job["status"], job["attempts"]), ("failed", 3))
        self.assertIn("JSON", job["last_error"])
        self.assertEqual(memories(self.settings), [])
        # as mensagens originais seguem intactas
        conn = connect(self.settings.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 3)
        conn.close()
        report, _ = self.run_with(extraction(), force=True)     # job falho não é reprocessado sozinho
        self.assertEqual(report["processed"], [])

    def test_generic_model_error_counts_as_an_attempt(self):
        report, _ = self.run_with(ModelError("500"))
        self.assertEqual(report["processed"][0]["attempts"], 1)

    def test_write_failure_rolls_back_everything(self):
        reply = extraction(facts=[fact("Fato um sobre o gato", [self.ids[0]]), fact("Fato dois sobre outra coisa", [self.ids[2]])])
        real = store.create_memory
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("disco cheio")
            return real(*a, **kw)

        with mock.patch("app.memory.consolidation.store.create_memory", side_effect=flaky):
            report, _ = self.run_with(reply)
        self.assertEqual(report["processed"][0]["status"], "pending")
        self.assertEqual(memories(self.settings), [])          # nem o episódio ficou pela metade
        self.assertIn("disco cheio", jobs(self.settings)[0]["last_error"])

    def test_sensitive_content_never_reaches_memory(self):
        reply = extraction(facts=[fact("A senha do banco do usuário é 1234abcd", [self.ids[0]])])
        self.run_with(reply)
        self.assertEqual(memories(self.settings, kind="semantic"), [])

    def test_only_one_consolidation_at_a_time(self):
        c = Consolidator(self.settings, ScriptedProvider([]), clock=LATER)
        self.assertTrue(c._lock.acquire(blocking=False))
        try:
            self.assertEqual(c.run_once()["status"], "busy")
        finally:
            c._lock.release()


class CrossEpisodeTests(DbTestCase):
    def two_episodes(self, first_facts, second_facts, first_notes=(), second_notes=()):
        a = seed_chat(self.settings, [("user", "Primeiro episódio: conversa longa o bastante para contar", 0)])
        b = seed_chat(self.settings, [("user", "Segundo episódio: outra conversa longa o bastante para contar", 300)])
        provider = ScriptedProvider([extraction(facts=first_facts(a[0]), notes=first_notes and first_notes(a[0]) or ()),
                                     extraction(facts=second_facts(b[0]), notes=second_notes and second_notes(b[0]) or ())])
        c = Consolidator(self.settings, provider, clock=LATER)
        c.run_once()
        return a, b, provider

    def test_same_fact_in_two_episodes_is_merged_with_recurrence_and_both_sources(self):
        a, b, _ = self.two_episodes(
            lambda m: [fact("O usuário tem um gato chamado Mia", [m])],
            lambda m: [fact("O usuário tem um gato, chamado Mia.", [m])],
        )
        facts = memories(self.settings, kind="semantic")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["recurrence"], 2)
        detail = MemoryService(self.settings).get(facts[0]["id"])
        self.assertEqual({s["message_id"] for s in detail["sources"]}, {a[0], b[0]})
        episodes = memories(self.settings, kind="episodic")
        self.assertEqual(len(episodes), 2)
        part_of = {l["to_id"] for l in detail["links"] if l["relation"] == "part_of"}
        self.assertEqual(part_of, {e["id"] for e in episodes})   # fato ligado aos dois episódios
        follows = {(l["from_id"], l["to_id"]) for e in episodes
                   for l in MemoryService(self.settings).get(e["id"])["links"] if l["relation"] == "follows"}
        self.assertEqual(len(follows), 1)                          # o segundo episódio segue o primeiro

    def test_user_statement_confirms_an_earlier_hypothesis(self):
        self.two_episodes(
            lambda m: [fact("O usuário gosta de jazz", [m], kind="inferred")],
            lambda m: [fact("O usuário gosta de jazz", [m], kind="stated")],
        )
        f = memories(self.settings, kind="semantic")[0]
        self.assertEqual((f["epistemic"], f["origin"]), ("confirmed", "user_stated"))
        hist = [h["action"] for h in MemoryService(self.settings).get(f["id"])["history"]]
        self.assertIn("epistemic_changed", hist)

    def test_new_user_statement_supersedes_old_one_but_keeps_it(self):
        a = seed_chat(self.settings, [("user", "Primeiro episódio: conversa longa o bastante para contar", 0)])
        Consolidator(self.settings, ScriptedProvider([extraction(facts=[fact("O usuário mora no Porto agora", [a[0]])])]),
                     clock=LATER).run_once()
        b = seed_chat(self.settings, [("user", "Segundo episódio: outra conversa longa o bastante para contar", 300)])
        old_id = memories(self.settings, kind="semantic")[0]["id"]
        Consolidator(self.settings, ScriptedProvider([extraction(facts=[fact("O usuário mora em Braga agora", [b[0]], contradicts=[old_id])])]),
                     clock=LATER).run_once()
        old = MemoryService(self.settings).get(old_id)
        self.assertEqual(old["status"], "superseded")              # preservado, não apagado
        self.assertIn("Porto", old["content"])
        active = MemoryService(self.settings).list(kind="semantic")
        self.assertEqual([m["content"] for m in active], ["O usuário mora em Braga agora"])
        self.assertEqual(old["superseded_by"], active[0]["id"])

    def test_hypothesis_never_overrides_a_confirmed_fact_it_only_records_uncertainty(self):
        a = seed_chat(self.settings, [("user", "Primeiro episódio: conversa longa o bastante para contar", 0)])
        Consolidator(self.settings, ScriptedProvider([extraction(facts=[fact("O usuário mora no Porto agora", [a[0]])])]),
                     clock=LATER).run_once()
        old_id = memories(self.settings, kind="semantic")[0]["id"]
        b = seed_chat(self.settings, [("user", "Segundo episódio: outra conversa longa o bastante para contar", 300)])
        Consolidator(self.settings, ScriptedProvider([extraction(
            facts=[fact("O usuário mora em Braga agora", [b[0]], kind="inferred", contradicts=[old_id])])]),
            clock=LATER).run_once()
        svc = MemoryService(self.settings)
        self.assertEqual(svc.get(old_id)["status"], "active")       # o fato confirmado permanece
        new = [m for m in svc.list(kind="semantic") if m["id"] != old_id][0]
        self.assertEqual(new["epistemic"], "hypothesis")
        self.assertIn("contradiction_noted", [h["action"] for h in svc.get(new["id"])["history"]])
        self.assertIn("contradiction_noted", [h["action"] for h in svc.get(old_id)["history"]])

    def test_unrelated_contradiction_claim_does_not_supersede(self):
        a = seed_chat(self.settings, [("user", "Primeiro episódio: conversa longa o bastante para contar", 0)])
        Consolidator(self.settings, ScriptedProvider([extraction(facts=[fact("O usuário trabalha com engenharia civil", [a[0]])])]),
                     clock=LATER).run_once()
        old_id = memories(self.settings, kind="semantic")[0]["id"]
        b = seed_chat(self.settings, [("user", "Segundo episódio: outra conversa longa o bastante para contar", 300)])
        Consolidator(self.settings, ScriptedProvider([extraction(
            facts=[fact("O usuário adora cozinhar massas", [b[0]], contradicts=[old_id])])]), clock=LATER).run_once()
        self.assertEqual(MemoryService(self.settings).get(old_id)["status"], "active")  # sem assunto em comum

    def test_identity_observation_needs_recurrence_across_episodes(self):
        svc = MemoryService(self.settings)
        note = lambda m: [{"text": "O usuário prefere respostas curtas", "evidence": [m], "confidence": 0.6}]
        for i in range(3):
            seed = seed_chat(self.settings, [("user", f"Episódio {i}: conversa longa o bastante para contar bem", 600 * i)])
            Consolidator(self.settings, ScriptedProvider([extraction(notes=note(seed[0]))]), clock=LATER).run_once()
            if i < 2:
                self.assertEqual(svc.identity_candidates(), [])   # uma ou duas vezes ainda não é traço
        cands = svc.identity_candidates()
        self.assertEqual([(c["recurrence"], c["episodes"]) for c in cands], [(3, 3)])


class CrossSessionRecallTests(DbTestCase):
    """Critério da Fase 3: o que foi dito numa sessão é lembrado em outra, mesmo fora do contexto atual."""

    def test_fact_from_an_old_session_reaches_the_model_in_a_new_one(self):
        old = seed_chat(self.settings, [
            ("user", "Tenho um gato chamado Mia e adoro ela, dorme no meu colo todo dia", 0),
            ("assistant", "Que fofo!", 1),
        ])
        Consolidator(self.settings, ScriptedProvider([extraction(
            summary="O usuário falou do gato.", facts=[fact("O usuário tem um gato chamado Mia", [old[0]])])]),
            clock=LATER).run_once()

        # "nova sessão": novas instâncias de tudo; o contexto do chat só guarda as 2 últimas mensagens
        settings = self.make_settings(context_messages=2)
        provider = FakeProvider()
        chat = ChatService(settings, provider, memory=MemoryService(settings))
        for i in range(3):
            chat.send_message(f"mensagem de enchimento número {i} sobre outro assunto qualquer")
        result = chat.send_message("você lembra o nome do meu gato?")

        system = prompt_of(provider.calls[-1])
        self.assertIn("O usuário tem um gato chamado Mia", system)
        self.assertIn("[confirmado", system)
        self.assertTrue(result.memories_used)
        context_texts = " ".join(spoken(m.content) for m in provider.calls[-1][1:])   # só as falas, sem o bloco de contexto
        self.assertNotIn("Mia", context_texts)             # a informação NÃO estava no contexto da conversa
        self.assertGreater(MemoryService(settings).get(result.memories_used[0])["access_count"], 0)

    def test_hypothesis_is_labeled_as_such_for_the_model(self):
        ids = seed_chat(self.settings, [("user", "Escuto muito jazz quando trabalho em casa o dia todo", 0)])
        Consolidator(self.settings, ScriptedProvider([extraction(
            facts=[fact("O usuário talvez goste de jazz", [ids[0]], kind="inferred")])]), clock=LATER).run_once()
        provider = FakeProvider()
        ChatService(self.make_settings(context_messages=1), provider, memory=MemoryService(self.settings)).send_message(
            "que música combina com jazz?")
        self.assertIn("[hipótese", prompt_of(provider.calls[-1]))

    def test_memory_failure_never_breaks_the_chat(self):
        class Broken(MemoryService):
            def block_for_chat(self, *a, **kw):
                raise RuntimeError("memória corrompida")
        provider = FakeProvider()
        result = ChatService(self.settings, provider, memory=Broken(self.settings)).send_message("oi, tudo bem por aí?")
        self.assertEqual(result.reply, "eco: oi, tudo bem por aí?")
        self.assertEqual(result.memories_used, [])


class WorkerTests(DbTestCase):
    def test_tick_waits_for_idle_chat(self):
        seed_chat(self.settings, [("user", "Conversa antiga o bastante para virar uma memória real", 0)])
        provider = ScriptedProvider([extraction(facts=[fact("O usuário tem uma conversa antiga", [1])])])
        gate = ActivityGate()
        worker = MemoryWorker(Consolidator(self.settings, provider, clock=LATER), gate, interval=0.05, idle_seconds=0.2)
        with gate.chat():
            self.assertIsNone(worker.tick())                   # chat em andamento: não consolida
        self.assertIsNone(worker.tick())                       # acabou de falar: ainda não está ocioso
        import time
        time.sleep(0.25)
        report = worker.tick()
        self.assertEqual(report["processed"][0]["status"], "done")

    def test_background_thread_runs_and_stops_cleanly(self):
        import time
        ids = seed_chat(self.settings, [("user", "Conversa antiga o bastante para virar uma memória real", 0)])
        provider = ScriptedProvider([extraction(facts=[fact("O usuário tem uma conversa antiga", [ids[0]])])])
        worker = MemoryWorker(Consolidator(self.settings, provider, clock=LATER), ActivityGate(), interval=0.05, idle_seconds=0)
        worker.start()
        deadline = time.time() + 5
        while time.time() < deadline and not memories(self.settings, kind="episodic"):
            time.sleep(0.05)
        worker.stop()
        self.assertEqual(len(memories(self.settings, kind="episodic")), 1)
        self.assertFalse(worker._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
