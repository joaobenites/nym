"""Exportação de exemplos para o LoRA do Nym (app/training/export.py): só registros reais, com filtros."""
import json

from app.database.connection import connect, transaction
from app.life import diary as diary_mod
from app.training import export as ex
from tests.fakes import DbTestCase
from tests.memtools import BASE, at, seed_chat


def sql(settings, query, *args):
    conn = connect(settings.db_path)
    try:
        with transaction(conn):
            conn.execute(query, args)
    finally:
        conn.close()


class ExportTests(DbTestCase):
    def build(self):
        conn = connect(self.settings.db_path)
        try:
            return ex.build(conn, None)
        finally:
            conn.close()

    def test_chat_examples_with_context_filters_and_likes(self):
        ids = seed_chat(self.settings, [
            ("assistant", "oi! (iniciativa solta antes de qualquer fala dele)", 0),
            ("user", "oi Nym", 1), ("assistant", "E aí, João! Tudo certo?", 2),
            ("user", "bora desenhar?", 3), ("assistant", "Bora! Tô pensando num Digimon de dados.", 4),
            ("user", "e aquilo?", 5), ("assistant", "Como uma IA, não tenho opinião.", 6),
            ("user", "e agora?", 7), ("assistant", "Achei chato, sei lá.", 8),
            ("user", "minha senha é hunter2", 9), ("assistant", "Guarda isso só pra você, hein.", 10),
        ])
        sql(self.settings, "INSERT INTO audit_log (created_at, event, detail) VALUES (?, 'chat.reply', ?)", at(6),
            json.dumps({"assistant_message_id": ids[6], "voice": {"issues": ["fala_como_ia"], "retried": False}}))
        sql(self.settings, "INSERT INTO feedback (target, target_id, value, created_at) VALUES ('message', ?, 1, ?)", ids[4], at(4))
        sql(self.settings, "INSERT INTO feedback (target, target_id, value, created_at) VALUES ('message', ?, -1, ?)", ids[8], at(8))
        train, evals, skipped = self.build()
        got = [e["messages"][-1]["content"] for e in train + evals]
        self.assertEqual(got.count("Bora! Tô pensando num Digimon de dados."), 2)       # curtida pesa em dobro
        self.assertIn("E aí, João! Tudo certo?", got)
        self.assertNotIn("Como uma IA, não tenho opinião.", got)                        # vício de voz sem correção
        self.assertNotIn("Achei chato, sei lá.", got)                                   # 👎
        self.assertFalse(any("hunter2" in m["content"] for e in train + evals for m in e["messages"]))
        self.assertEqual(skipped["vício de voz sem correção"], 1)
        liked = next(e for e in train if e["messages"][-1]["content"].startswith("Bora"))
        roles = [m["role"] for m in liked["messages"]]
        self.assertEqual(roles[0], "system")
        self.assertEqual(roles[1], "user")                                              # contexto começa numa fala dele
        self.assertTrue(all(a != b for a, b in zip(roles[1:], roles[2:])))              # alterna (formato do Gemma)

    def test_diary_page_uses_the_real_prompt_and_facts(self):
        sql(self.settings, "INSERT INTO diary (day, text, tomorrow, facts, created_at) VALUES (?, ?, ?, ?, ?)",
            BASE.date().isoformat(), "Hoje desenhei uma criatura de dados e curti as cores.", "desenhar outra",
            json.dumps({"done": [], "started": [], "stuck": [], "creations": [{"id": 1, "kind": "drawing", "title": "Criatura",
                        "version": 1}], "watched": [], "seen": [], "searches": [], "dreams": [], "talk": {"user": 0, "nym": 0,
                        "initiative": 0}, "topics": [], "lessons": [], "reactions": {"likes": 0, "dislikes": 0}}), at(0))
        train, evals, _ = self.build()
        page = next(e for e in train + evals if e["meta"]["source"] == "diary")
        self.assertEqual(page["messages"][0]["content"], diary_mod.PROMPT)
        self.assertIn('Criou: desenho "Criatura"', page["messages"][1]["content"])
        self.assertTrue(page["messages"][2]["content"].endswith("Amanhã: desenhar outra"))

    def test_eval_split_never_shares_an_example_with_train(self):
        items = []
        for i in range(30):
            items += [("user", f"pergunta número {i} sobre desenhos", i * 2), ("assistant", f"resposta número {i}, bora!", i * 2 + 1)]
        seed_chat(self.settings, items)
        train, evals, _ = self.build()
        self.assertTrue(evals)
        key = lambda e: e["meta"]["message_id"]  # noqa: E731
        self.assertFalse({key(e) for e in train} & {key(e) for e in evals})
        self.assertIn("é pouco", ex.report(train, evals, {}))

    def test_cli_writes_files_inside_data(self):
        seed_chat(self.settings, [("user", "oi", 0), ("assistant", "opa!", 1)])
        out = self.settings.data_dir / "training"
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"NYM_DATA_DIR": str(self.settings.data_dir)}):
            ex.main(["--sem-sintetico"])
        lines = (out / "nym_train.jsonl").read_text(encoding="utf-8").splitlines() + \
            (out / "nym_eval.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0])["messages"][-1]["content"], "opa!")
        self.assertTrue((out / "relatorio.txt").exists())


class SecondRoundFilterTests(DbTestCase):
    """O 1º LoRA (19/10) aprendeu o nome "Claudio" de uma conversa de teste e quebrou o JSON do plano."""

    def build(self):
        conn = connect(self.settings.db_path)
        try:
            return ex.build(conn, None)
        finally:
            conn.close()

    def test_tester_conversation_and_ungrounded_claims_are_left_out(self):
        seed_chat(self.settings, [
            ("user", "Oi Nym, aqui é o Claudio (Claude, testando você)", 0), ("assistant", "Oi, Claudio! Tudo certo.", 1),
            ("user", "e aí, tudo bem?", 2), ("assistant", "O João me avisou que você ia trazer pizza hoje.", 3),
            ("user", "bora desenhar um digimon?", 4), ("assistant", "Bora! Tô pensando num de dados.", 5),
            ("user", "e que cor?", 6), ("assistant", "Azul e roxo, que tal?", 7),
        ])
        train, evals, skipped = self.build()
        got = [e["messages"][-1]["content"] for e in train + evals]
        self.assertEqual(got, ["Azul e roxo, que tal?"])     # "Bora" ainda tinha a fala do teste no contexto
        self.assertGreaterEqual(skipped["conversa de teste (outra IA)"], 3)

    def test_claim_without_basis_in_what_he_said_is_left_out(self):
        seed_chat(self.settings, [("user", "e aí, tudo bem?", 0), ("assistant", "O João me avisou que você ia trazer pizza hoje.", 1),
                                  ("user", "trouxe pizza sim", 2), ("assistant", "Você disse que trouxe pizza, que massa!", 3)])
        train, evals, skipped = self.build()
        self.assertEqual([e["messages"][-1]["content"] for e in train + evals], ["Você disse que trouxe pizza, que massa!"])
        self.assertEqual(skipped["lembrança sem base no que você disse"], 1)

    def test_successful_first_round_plan_becomes_a_json_example(self):
        conn = connect(self.settings.db_path)
        with transaction(conn):
            gid = conn.execute("INSERT INTO goals (description, origin, priority, status, created_at, updated_at, mode) "
                               "VALUES ('Pesquisar sobre Digimon Tamers', 'nym', 3, 'done', ?, ?, 'task')", (at(0), at(0))).lastrowid
            for step, (tool, args, status) in enumerate([("web_search", {"query": "Digimon Tamers"}, "done")], 1):
                conn.execute("INSERT INTO tasks (goal_id, step, title, tool, args, risk, origin, status, created_at, updated_at) "
                             "VALUES (?, ?, 'Buscar', ?, ?, 'low', 'nym', ?, ?, ?)", (gid, step, tool, json.dumps(args), status, at(1), at(1)))
            bad = conn.execute("INSERT INTO goals (description, origin, priority, status, created_at, updated_at, mode) "
                               "VALUES ('Outro', 'nym', 3, 'done', ?, ?, 'task')", (at(0), at(0))).lastrowid
            conn.execute("INSERT INTO tasks (goal_id, step, title, tool, args, risk, origin, status, created_at, updated_at) "
                         "VALUES (?, 1, 'x', 'web_search', '{}', 'low', 'nym', 'failed', ?, ?)", (bad, at(1), at(1)))
        conn.close()
        train, evals, skipped = self.build()
        plans = [e for e in train + evals if e["meta"]["source"] == "plan"]
        self.assertEqual(len(plans), 1)
        self.assertIn("Responda SOMENTE com JSON", plans[0]["messages"][0]["content"])
        self.assertEqual(json.loads(plans[0]["messages"][-1]["content"])["steps"][0]["tool"], "web_search")
        self.assertEqual(skipped["plano que não deu certo de primeira"], 1)


class SyntheticTests(DbTestCase):
    def test_handwritten_examples_go_only_to_train_and_bad_lines_are_skipped(self):
        import tempfile
        from pathlib import Path
        seed_chat(self.settings, [("user", "oi", 0), ("assistant", "opa!", 1)])
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "s.jsonl"
            good = {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "oi"},
                                 {"role": "assistant", "content": "E aí!"}], "meta": {"id": "c1"}}
            f.write_text(json.dumps(good) + "\n{quebrado\n" + json.dumps({"messages": []}) + "\n", encoding="utf-8")
            conn = connect(self.settings.db_path)
            try:
                train, evals, skipped = ex.build(conn, f)
            finally:
                conn.close()
        self.assertEqual([e["meta"]["source"] for e in train if e["meta"]["source"] == "sintetico"], ["sintetico"])
        self.assertFalse(any(e["meta"]["source"] == "sintetico" for e in evals))
        self.assertEqual(skipped["exemplo escrito inválido"], 2)

    def test_shipped_file_is_valid(self):
        self.assertTrue(ex.SYNTHETIC.exists())
        items = ex.synthetic_examples(ex.SYNTHETIC, __import__("collections").Counter())
        self.assertGreaterEqual(len(items), 50)
        self.assertFalse(any(ex.TESTER.search(m["content"]) for e in items for m in e["messages"] if m["role"] != "system"))
