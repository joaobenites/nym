import unittest
from datetime import timedelta

from app.core.state import StateInputs, StateService, compute_state, energy_from_timeline, render_state_block
from app.database.connection import connect, transaction
from tests.fakes import DbTestCase
from tests.memtools import BASE, at, clock_at, seed_chat

NOW = BASE


def inp(**kw):
    base = dict(hours_since_last_chat=1.0, messages_last_2h=0, chat_days_last_30=0, total_user_messages=0,
                open_questions=0, pending_explorations=0, unconfirmed_hypotheses=0, new_memories_since_dream=0)
    base.update(kw)
    return StateInputs(**base)


class ComputeTests(unittest.TestCase):
    def test_all_values_are_bounded(self):
        for kw in (dict(), dict(messages_last_2h=999, open_questions=99, pending_explorations=99, chat_days_last_30=99,
                                total_user_messages=99999, unconfirmed_hypotheses=999, new_memories_since_dream=999,
                                hours_since_last_chat=9999.0), dict(hours_since_last_chat=None)):
            s = compute_state(inp(**kw), NOW)
            for v in (s.energy, s.curiosity, s.bond):
                self.assertTrue(0 <= v <= 1, (kw, v))

    def test_rest_raises_energy_and_activity_lowers_it(self):
        rested = compute_state(inp(energy_reserve=energy_from_timeline([10.0], [])), NOW)
        marathon = [i * 0.02 for i in range(300)]   # 300 mensagens suas em 6 h, sem parar
        busy = compute_state(inp(energy_reserve=energy_from_timeline(marathon, [])), NOW)
        self.assertGreater(rested.energy, busy.energy)
        self.assertEqual(busy.label, "cansado")
        self.assertEqual(rested.label, "descansado")

    def test_normal_conversation_does_not_tire(self):
        # o bug real: com ~1 mensagem a cada 4 min por horas, ele ficava "cansado" em 148 de 150 mensagens
        steady = [i * (4 / 60) for i in range(60)]   # 60 mensagens em 4 h
        self.assertGreaterEqual(compute_state(inp(energy_reserve=energy_from_timeline(steady, [])), NOW).energy, 0.30)
        short_chat = [i * 0.05 for i in range(15)]
        self.assertEqual(compute_state(inp(energy_reserve=energy_from_timeline(short_chat, [])), NOW).label, "descansado")

    def test_short_pauses_recover_and_own_messages_never_count(self):
        burst = [1.5 + i * 0.01 for i in range(40)]
        later = energy_from_timeline(burst, [])
        right_after = energy_from_timeline([h - 1.5 for h in burst], [])
        self.assertGreater(later, right_after)   # 1,5 h de pausa já recupera

    def test_dream_counts_only_as_a_real_nap(self):
        msgs = [3.0 + i * 0.02 for i in range(40)]
        idle_dream = energy_from_timeline(msgs, [1.0])        # sonhou 2 h depois da última fala
        busy_dream = energy_from_timeline(msgs + [0.5], [2.95])  # sonho no meio da conversa
        no_dream = energy_from_timeline(msgs + [0.5], [])
        self.assertGreater(idle_dream, energy_from_timeline(msgs, []))
        self.assertAlmostEqual(busy_dream, no_dream, places=4)

    def test_old_backlog_does_not_make_him_curious_forever(self):
        backlog_only = compute_state(inp(open_questions=26, unconfirmed_hypotheses=70), NOW)
        self.assertLess(backlog_only.curiosity, 0.6)
        satisfied = compute_state(inp(open_questions=4, new_memories_since_dream=12, explored_last_3h=5), NOW)
        hungry = compute_state(inp(open_questions=4, new_memories_since_dream=12), NOW)
        self.assertLess(satisfied.curiosity, hungry.curiosity)

    def test_open_questions_and_novelty_raise_curiosity(self):
        calm = compute_state(inp(), NOW)
        curious = compute_state(inp(open_questions=4, pending_explorations=2, new_memories_since_dream=12), NOW)
        self.assertGreater(curious.curiosity, calm.curiosity)
        self.assertEqual(curious.label, "curioso")
        self.assertTrue(any("pergunta" in r for r in curious.reasons))

    def test_bond_grows_with_regular_use_and_fades_with_absence(self):
        new = compute_state(inp(), NOW)
        regular = compute_state(inp(chat_days_last_30=25, total_user_messages=400), NOW)
        gone = compute_state(inp(chat_days_last_30=25, total_user_messages=400, hours_since_last_chat=24 * 90), NOW)
        self.assertGreater(regular.bond, new.bond)
        self.assertLess(gone.bond, regular.bond)
        self.assertGreater(gone.bond, 0)   # esmaece, não zera

    def test_deterministic_and_honest_about_what_it_is(self):
        a, b = compute_state(inp(open_questions=2), NOW), compute_state(inp(open_questions=2), NOW)
        self.assertEqual(a, b)
        self.assertIn("não é sentimento", a.note)
        block = render_state_block(a)
        self.assertIn("não um sentimento", block)
        self.assertIn("estado calculado", block)

    def test_never_talked_is_handled(self):
        s = compute_state(inp(hours_since_last_chat=None), NOW)
        self.assertGreaterEqual(s.energy, 0.8)


class GatherTests(DbTestCase):
    def test_reads_real_data(self):
        seed_chat(self.settings, [("user", "oi", 0), ("assistant", "e aí", 1), ("user", "tudo bem", 30)])
        conn = connect(self.settings.db_path)
        with transaction(conn):
            conn.execute("INSERT INTO dream_cycles (trigger, status, started_at) VALUES ('manual', 'completed', ?)", (at(0),))
            cid = conn.execute("SELECT id FROM dream_cycles").fetchone()[0]
            conn.execute("INSERT INTO dream_items (cycle_id, kind, content, status, created_at) VALUES (?, 'open_question', 'q?', 'open', ?)", (cid, at(1)))
            conn.execute("INSERT INTO exploration_tasks (question, status, created_at, updated_at) VALUES ('x?', 'pending', ?, ?)", (at(1), at(1)))
        conn.close()
        state = StateService(self.settings, clock=clock_at(90)).current()
        i = state.inputs
        self.assertEqual((i["open_questions"], i["pending_explorations"], i["total_user_messages"]), (1, 1, 2))
        self.assertAlmostEqual(i["hours_since_last_chat"], 1.0, places=1)
        self.assertEqual(i["messages_last_2h"], 3)
        self.assertEqual(i["user_messages_last_hour"], 1)   # só as suas; a resposta dele não cansa
        self.assertIsNotNone(i["energy_reserve"])
        self.assertGreater(state.energy, 0.75)

    def test_empty_database_works(self):
        s = StateService(self.settings).current()
        self.assertIsNone(s.inputs["hours_since_last_chat"])


if __name__ == "__main__":
    unittest.main()
