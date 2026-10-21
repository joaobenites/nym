import json
import random
import unittest
from datetime import datetime, timedelta, timezone

from app.core import pet
from app.database.connection import connect, transaction
from tests.fakes import DbTestCase

T0 = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)   # meio-dia: sem desconto noturno


class PetBase(DbTestCase):
    def setUp(self):
        super().setUp()
        self.conn = connect(self.settings.db_path)
        self.addCleanup(self.conn.close)
        with transaction(self.conn):
            pet.advance(self.conn, T0)

    def at(self, minutes):
        return T0 + timedelta(minutes=minutes)

    def act(self, action, minutes, **kw):
        with transaction(self.conn):
            return pet.act(self.conn, action, self.at(minutes), **kw)

    def st(self, minutes=None):
        with transaction(self.conn):
            return pet.status(self.conn, self.at(minutes if minutes is not None else 0))

    def set(self, **cols):
        with transaction(self.conn):
            self.conn.execute(f"UPDATE pet_state SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = 1", tuple(cols.values()))


class DecayTests(PetBase):
    def test_starts_with_known_values_and_decays_with_time(self):
        a = self.st(0)
        b = self.st(120)   # 2 h de dia
        self.assertAlmostEqual(a["fullness"] - b["fullness"], 8.0, delta=0.2)
        self.assertAlmostEqual(a["fun"] - b["fun"], 6.0, delta=0.2)

    def test_night_decays_slower(self):
        night = T0.replace(hour=2)
        with transaction(self.conn):
            self.conn.execute("UPDATE pet_state SET updated_at = ?", (night.isoformat(),))
            pet.advance(self.conn, night + timedelta(hours=2))
        row = self.conn.execute("SELECT fullness FROM pet_state").fetchone()
        self.assertAlmostEqual(70 - row["fullness"], 4.0 * 2 * pet.NIGHT_FACTOR, delta=0.2)

    def test_catch_up_is_capped_and_never_below_floor(self):
        info = self.st(60 * 24 * 10)   # 10 dias com o programa fechado
        self.assertGreaterEqual(info["fullness"], pet.FLOOR)
        self.assertGreater(info["fullness"], 70 - 4.0 * pet.CATCH_UP_CAP_HOURS - 1)

    def test_pause_freezes_and_resume_does_not_charge_the_gap(self):
        self.act("pause", 0)
        self.assertEqual(self.st(600)["fullness"], 70.0)
        self.act("resume", 601)
        info = self.st(602)
        self.assertGreater(info["fullness"], 69.0)
        self.assertEqual(info["open_calls"], [])

    def test_clock_going_backwards_does_not_decay(self):
        self.assertEqual(self.st(-30)["fullness"], 70.0)


class ActionTests(PetBase):
    def test_feed_raises_fullness_and_schedules_a_mess(self):
        r = self.act("feed", 0)
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(self.st(0)["fullness"], 100.0, delta=0.01)   # 70 + 35 -> teto
        info = self.st(int(pet.MESS_DELAY_HOURS * 60) + 5)
        self.assertEqual(info["messes"], 1)

    def test_feed_refused_when_full(self):
        self.set(fullness=95.0)
        r = self.act("feed", 0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "full")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM pet_events WHERE kind = 'refused'").fetchone()[0], 1)

    def test_snack_costs_discipline(self):
        self.set(fullness=50.0, discipline=40.0)
        r = self.act("snack", 0)
        self.assertTrue(r["ok"])
        self.assertLess(self.st(0)["discipline"], 40.0)

    def test_clean_removes_messes_and_cleans(self):
        self.act("feed", 0)
        info = self.st(200)
        self.assertEqual(info["messes"], 1)
        r = self.act("clean", 200)
        self.assertTrue(r["ok"])
        after = self.st(200)
        self.assertEqual(after["messes"], 0)
        self.assertGreater(after["hygiene"], info["hygiene"])
        self.assertFalse(self.act("clean", 201)["ok"])        # já limpo

    def test_play_rps_is_decided_by_code_and_uses_rng(self):
        rng = random.Random(1)
        r = self.act("play", 0, choice="pedra", rng=rng)
        self.assertTrue(r["ok"])
        game = r["game"]
        beats = {"pedra": "tesoura", "tesoura": "papel", "papel": "pedra"}
        if game["you"] == game["nym"]:
            self.assertEqual(game["result"], "empate")
        elif beats[game["you"]] == game["nym"]:
            self.assertEqual(game["result"], "voce")
        else:
            self.assertEqual(game["result"], "nym")
        self.assertGreater(self.st(0)["fun"], 70.0 - 1)

    def test_play_invalid_choice_is_an_error_and_changes_nothing(self):
        before = self.st(0)
        with self.assertRaises(pet.PetError):
            self.act("play", 0, choice="lagarto")
        self.assertEqual(self.st(0)["fun"], before["fun"])

    def test_play_cooldown_and_hunger_refusal(self):
        self.assertTrue(self.act("play", 0, choice="papel")["ok"])
        self.assertEqual(self.act("play", 1, choice="papel")["reason"], "cooldown")
        self.set(fullness=10.0)
        self.assertEqual(self.act("play", 30, choice="papel")["reason"], "hungry")

    def test_train_raises_discipline_with_cooldown(self):
        self.set(discipline=30.0)
        self.assertTrue(self.act("train", 0)["ok"])
        self.assertGreater(self.st(0)["discipline"], 37.0)
        self.assertEqual(self.act("train", 5)["reason"], "cooldown")
        self.assertTrue(self.act("train", 40)["ok"])

    def test_train_refused_when_hungry_or_bored(self):
        self.set(fullness=20.0)
        r = self.act("train", 0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no_mood")

    def test_pet_has_diminishing_returns(self):
        self.set(affection=10.0)
        gains = []
        last = 10.0
        for i in range(8):
            self.act("pet", 0)
            now = self.st(0)["affection"]
            gains.append(round(now - last, 1))
            last = now
        self.assertGreater(gains[0], 5.0)
        self.assertLessEqual(gains[-1], 1.0)

    def test_sleep_blocks_actions_until_woken(self):
        self.assertTrue(self.act("sleep", 0, hours=2)["ok"])
        self.assertEqual(self.act("feed", 10)["reason"], "sleeping")
        self.assertTrue(self.st(10)["sleeping"])
        self.assertFalse(self.st(130)["sleeping"])           # acordou sozinho
        self.assertTrue(self.act("feed", 131)["ok"])

    def test_sleep_slows_decay_and_manual_wake(self):
        self.act("sleep", 0, hours=4)
        a = self.st(0)["fullness"]
        b = self.st(120)["fullness"]
        self.assertAlmostEqual(a - b, 4.0 * 2 * pet.SLEEP_FACTOR, delta=0.2)
        self.assertTrue(self.act("wake", 121)["ok"])
        self.assertFalse(self.act("wake", 122)["ok"])

    def test_sleep_validates_hours(self):
        for bad in (0, 11, "x", True):
            with self.assertRaises(pet.PetError):
                self.act("sleep", 0, hours=bad)

    def test_paused_refuses_care(self):
        self.act("pause", 0)
        self.assertEqual(self.act("feed", 5)["reason"], "paused")

    def test_unknown_action_is_an_error(self):
        with self.assertRaises(pet.PetError):
            self.act("fly", 0)


class CallTests(PetBase):
    def calls(self, status=None):
        sql = "SELECT * FROM pet_calls" + (" WHERE status = ?" if status else "")
        return self.conn.execute(sql, (status,) if status else ()).fetchall()

    def test_hunger_opens_one_call_and_feeding_answers_it(self):
        self.set(fullness=25.0)
        self.st(1)
        self.st(2)
        self.assertEqual([c["need"] for c in self.calls("open")], ["fome"])
        with transaction(self.conn):
            self.assertEqual(pet.queue_calls(self.conn, self.at(2)), 1)
            self.assertEqual(pet.queue_calls(self.conn, self.at(3)), 0)     # uma vez só
        self.act("feed", 5)
        self.assertEqual(self.calls("answered")[0]["need"], "fome")
        self.assertEqual(self.conn.execute("SELECT status FROM pending_messages WHERE kind = 'pet_call'").fetchone()[0], "dismissed")

    def test_unanswered_call_becomes_a_care_mistake_once(self):
        self.set(fullness=25.0)
        self.st(1)
        self.st(1 + int(pet.CALL_GRACE_MIN) + 5)
        self.assertEqual(len(self.calls("missed")), 1)
        n = self.conn.execute("SELECT COUNT(*) FROM pet_events WHERE kind = 'care_mistake'").fetchone()[0]
        self.assertEqual(n, 1)
        self.st(1 + int(pet.CALL_GRACE_MIN) + 30)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM pet_events WHERE kind = 'care_mistake'").fetchone()[0], 1)

    def test_no_immediate_reopen_after_a_miss(self):
        self.set(fullness=6.0)
        self.st(1)
        self.st(80)
        self.assertEqual(len(self.calls("open")), 0)
        self.st(80 + int(pet.CALL_REOPEN_HOURS * 60) + 5)
        self.assertEqual(len(self.calls("open")), 1)

    def test_sleeping_does_not_call(self):
        self.act("sleep", 0, hours=3)
        self.set(fullness=20.0)
        self.st(10)
        self.assertEqual(self.calls("open"), [])

    def test_pause_keeps_calls_from_turning_into_mistakes(self):
        self.set(fullness=25.0)
        self.st(1)
        self.act("pause", 2)
        self.st(600)
        self.assertEqual(self.calls("missed"), [])


class ReadTests(PetBase):
    def test_mood_and_traits(self):
        self.assertEqual(self.st(0)["mood"], "tranquilo")
        self.set(fullness=20.0)
        self.assertEqual(self.st(0)["mood"], "com fome")
        self.set(fullness=90.0, fun=90.0, hygiene=90.0, affection=80.0, discipline=75.0)
        info = self.st(0)
        self.assertEqual(info["mood"], "feliz")
        self.assertIn("disciplinado", info["traits"])
        self.assertIn("grudado em você", info["traits"])

    def test_render_block_is_honest_and_has_real_data(self):
        self.set(fullness=20.0)
        info = self.st(0)
        calm = pet.render_block(self.st(0) | {"mood": "tranquilo", "open_calls": [], "messes": 0, "sleeping": False, "paused": False}, self.at(0))
        self.assertIn("Não precisa comentar", calm)
        text = pet.render_block(info, self.at(0))
        self.assertIn("não sentimentos", text)
        self.assertIn("com fome", text)
        self.assertIn("Nunca invente", text)
        self.assertIn("fome", text.split("chamando por cuidado")[1])

    def test_care_days_counts_distinct_days(self):
        self.act("pet", 0)
        self.act("pet", 5)
        self.act("pet", 60 * 24 + 5)
        self.assertEqual(pet.care_days(self.conn), 2)


class MigrationTests(PetBase):
    def test_pending_messages_accepts_pet_call(self):
        with transaction(self.conn):
            self.conn.execute("INSERT INTO pending_messages (kind, topic, refs, status, created_at) VALUES "
                              "('pet_call', 'x', '[]', 'pending', '2026-01-01T00:00:00.000+00:00')")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM pending_messages WHERE kind = 'pet_call'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()



class AnswerTests(PetBase):
    def test_feeding_closes_the_hunger_call_even_if_still_below_margin(self):
        self.set(fullness=10.0)
        self.st(1)
        self.assertEqual(len(self.conn.execute("SELECT * FROM pet_calls WHERE status = 'open'").fetchall()), 1)
        self.act("snack", 2)    # sobe pouco a barriga, mas é o que ele pediu
        self.assertEqual(self.conn.execute("SELECT status FROM pet_calls").fetchone()[0], "answered")

    def test_sleeping_call_is_not_a_mistake(self):
        self.set(fullness=25.0)
        self.st(1)
        self.act("sleep", 2, hours=10)
        self.st(2 + 300)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM pet_events WHERE kind = 'care_mistake'").fetchone()[0], 0)


class ValidationOrderTests(PetBase):
    def test_invalid_choice_is_an_error_even_during_cooldown(self):
        self.assertTrue(self.act("play", 0, choice="papel")["ok"])
        with self.assertRaises(pet.PetError):
            self.act("play", 1, choice="lagarto")
