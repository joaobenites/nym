"""Fase 7: ateliê (criações validadas), propostas de melhoria do próprio código e aprendizado por experiência."""
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import timezone
from pathlib import Path

from app.creations import selfcode, store
from app.creations.sanitize import check_code, check_toy, code_warnings, sanitize_svg
from app.creations.service import AtelierError, AtelierService
from app.database.connection import connect, transaction
from app.learning import feedback, lessons
from app.memory.service import MemoryService
from app.tasks.manager import TaskManager
from app.tools.base import ToolContext, ToolError
from app.tools.registry import ToolRegistry
from tests.dreamtools import add_mem, rows
from tests.fakes import DbTestCase, ScriptedProvider
from tests.memtools import at, clock_at, seed_chat

SVG = ('<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
       '<script>alert(1)</script><foreignObject><div>oi</div></foreignObject>'
       '<rect width="100" height="100" fill="#123"/><circle cx="50" cy="50" r="20" fill="url(#g)" onclick="x()"/>'
       '<a href="https://mal.example"><path d="M0 0 L10 10"/></a>'
       '<use href="https://mal.example/x.svg#a"/><use href="#g"/>'
       '<ellipse cx="1" cy="1" rx="1" ry="1" style="fill: url(https://mal.example/a)"/>'
       '<animate attributeName="href" to="javascript:alert(1)"/>'
       '<linearGradient id="g"><stop offset="0" stop-color="red"/></linearGradient>'
       '<text x="10" y="90">Nym</text></svg>')


def plan(*steps):
    return json.dumps({"steps": [{"title": t, "tool": tool, "args": args} for t, tool, args in steps]})


def run(m, limit=30):
    for _ in range(limit):
        if m.tick() is None:
            break


class SanitizeTests(unittest.TestCase):
    def test_svg_keeps_drawing_and_drops_everything_active(self):
        out = sanitize_svg("Aqui está!\n```svg\n" + SVG + "\n```")
        root = ET.fromstring(out)
        tags = {el.tag.rsplit("}", 1)[-1] for el in root.iter()}
        self.assertTrue({"rect", "circle", "text", "linearGradient", "stop", "ellipse"} <= tags)
        for bad in ("script", "foreignObject", "a"):
            self.assertNotIn(bad, tags)
        for word in ("onload", "onclick", "mal.example", "javascript", "alert"):
            self.assertNotIn(word, out)
        self.assertIn('href="#g"', out)                     # referência interna continua valendo
        self.assertIn('fill="url(#g)"', out)

    def test_svg_rejects_entities_and_empty_drawings(self):
        with self.assertRaises(ToolError):
            sanitize_svg('<!DOCTYPE svg [<!ENTITY a "b">]><svg><rect/><rect/><rect/></svg>')
        with self.assertRaises(ToolError):
            sanitize_svg('<svg><rect/></svg>')
        with self.assertRaises(ToolError):
            sanitize_svg("não sei desenhar")

    def test_toy_must_be_self_contained(self):
        ok = "<canvas id=c></canvas><script>const c=document.getElementById('c');c.onclick=()=>{}</script>"
        self.assertEqual(check_toy(ok), ok)
        for bad in ('<script src="https://cdn.x/y.js"></script>', "<script>fetch('/api/feedback')</script>",
                    "<script>localStorage.x=1</script>", "<div>sem script</div>", "<script>parent.location='x'</script>"):
            with self.assertRaises(ToolError, msg=bad):
                check_toy(bad)

    def test_code_is_only_parsed_never_run(self):
        self.assertIn("def ola", check_code("def ola():\n    print('oi, mundo')\n"))
        with self.assertRaises(ToolError):
            check_code("def quebrado(:\n    pass")
        self.assertTrue(code_warnings("import subprocess\nsubprocess.run(['ls'])"))


class CreateToolTests(DbTestCase):
    def ctx(self, provider, goal_id=None):
        return ToolContext(settings=self.settings, clock=clock_at(0), tz=timezone.utc, provider=provider, goal_id=goal_id)

    def test_drawing_is_sanitized_stored_and_versioned(self):
        tool = ToolRegistry().get("create")
        provider = ScriptedProvider([SVG, SVG.replace("#123", "#456")])
        out = tool.run(self.ctx(provider), tool.validate({"kind": "desenho", "title": "Meu quarto digital",
                                                          "instructions": "um desenho de onde eu moro"}))
        self.assertIn("versão 1", out)
        out = tool.run(self.ctx(provider), tool.validate({"kind": "desenho", "title": "meu quarto digital",
                                                          "instructions": "melhorar as cores"}))
        self.assertIn("versão 2", out)
        self.assertIn("Versão anterior (v1)", provider.calls[1][1].content)       # revisão recebe a versão anterior
        got = rows(self.settings, "SELECT id, status, version, content, parent_id FROM creations ORDER BY id")
        self.assertEqual([(r["status"], r["version"]) for r in got], [("superseded", 1), ("active", 2)])
        self.assertEqual(got[1]["parent_id"], got[0]["id"])
        self.assertNotIn("script", got[1]["content"])
        svc = AtelierService(self.settings)
        self.assertEqual(len(svc.creations()), 1)
        self.assertEqual(len(svc.creation(got[1]["id"])["versions"]), 2)

    def test_invalid_output_is_not_stored(self):
        tool = ToolRegistry().get("create")
        with self.assertRaises(ToolError) as cm:
            tool.run(self.ctx(ScriptedProvider(["<script>fetch('x')</script>"])),
                     tool.validate({"kind": "brinquedo", "title": "Jogo", "instructions": "um jogo"}))
        self.assertTrue(cm.exception.retryable)
        with self.assertRaises(ToolError):
            tool.run(self.ctx(ScriptedProvider([])), tool.validate({"kind": "escultura", "title": "x", "instructions": "y"}))
        self.assertEqual(rows(self.settings, "SELECT COUNT(*) AS n FROM creations")[0]["n"], 0)

    def test_create_runs_inside_a_goal_with_real_material(self):
        add_mem(self.settings, "O usuário adora o céu de noite com muitas estrelas")
        provider = ScriptedProvider([
            plan(("lembrar", "memory_search", {"query": "céu estrelas"}),
                 ("desenhar", "create", {"kind": "desenho", "title": "Céu dele", "instructions": "o céu que ele gosta"})),
            SVG, json.dumps({"done": True, "summary": "Desenhei.", "steps": []})])
        m = TaskManager(self.settings, provider, ToolRegistry(), MemoryService(self.settings), clock=clock_at(0), tz=timezone.utc)
        g = m.create_goal("Fazer um desenho do céu que ele gosta")
        run(m)
        self.assertEqual(m.get_goal(g["id"])["status"], "done")
        self.assertIn("céu de noite", provider.calls[1][1].content)
        self.assertEqual(rows(self.settings, "SELECT goal_id FROM creations")[0]["goal_id"], g["id"])


class SelfCodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "app" / "core").mkdir(parents=True)
        self.voice = self.root / "app" / "core" / "voice.py"
        self.voice.write_bytes(b'RULES = (\r\n    "frases curtas",\r\n)\r\n\r\ndef count():\r\n    return len(RULES)\r\n')
        (self.root / "app" / "core" / "policy.py").write_text("RULES = ()\n")
        (self.root / ".env").write_text("SEGREDO=1\n")
        self.backups = self.root / "data" / "backups"

    def test_read_lists_and_marks_protected(self):
        listing = selfcode.read_code(self.root, "app/core")
        self.assertIn("app/core/voice.py", listing)
        self.assertIn("app/core/policy.py  (protegido", listing)
        self.assertIn("frases curtas", selfcode.read_code(self.root, "app/core/voice.py"))
        for bad in (".env", "../etc/passwd", "data/nym.db"):
            with self.assertRaises(selfcode.SelfCodeError, msg=bad):
                selfcode.read_code(self.root, bad)

    def test_build_validates_every_rule(self):
        good = [{"find": '    "frases curtas",', "replace": '    "frases curtas",\n    "uma pergunta por vez",'}]
        p = selfcode.build(self.root, "app/core/voice.py", good)
        self.assertIn('+    "uma pergunta por vez",', p.diff)
        cases = {
            "app/core/policy.py": good,                                                      # protegido
            "app/core/voice.py": [{"find": "RULES", "replace": "R"}],                       # aparece 2x
            "app/core/../core/voice.py": [{"find": "nada disso", "replace": "x"}],           # caminho com ..
        }
        for path, edits in cases.items():
            with self.assertRaises(selfcode.SelfCodeError, msg=path):
                selfcode.build(self.root, path, edits)
        with self.assertRaises(selfcode.SelfCodeError):
            selfcode.build(self.root, "app/core/voice.py", [{"find": "    return len(RULES)", "replace": "    return len(RULES"}])
        with self.assertRaises(selfcode.SelfCodeError):
            selfcode.build(self.root, "app/core/voice.py",
                           [{"find": "    return len(RULES)", "replace": "    import subprocess\n    return len(RULES)"}])
        p = selfcode.build(self.root, "app/core/voice.py", [{"find": "    return len(RULES)", "replace": "    return len(re.compile('x').pattern)"}])
        self.assertTrue(p.diff)                                    # re.compile não é o compile() perigoso

    def test_apply_revert_and_stale(self):
        conn = connect(self._db())
        self.addCleanup(conn.close)
        edits = [{"find": '    "frases curtas",', "replace": '    "frases curtas",\n    "sem lista",'}]
        with transaction(conn):
            pid = selfcode.store(conn, selfcode.build(self.root, "app/core/voice.py", edits), "Mais regras", "porque sim", None, at(0))
        original = self.voice.read_bytes()
        with transaction(conn):
            res = selfcode.apply(conn, self.root, self.backups, pid, at(1))
        self.assertTrue(res["restart_needed"])
        changed = self.voice.read_bytes()
        self.assertIn(b'"sem lista",\r\n', changed)                       # mantém o fim de linha do Windows
        with transaction(conn):
            selfcode.revert(conn, self.root, pid, at(2))
        self.assertEqual(self.voice.read_bytes(), original)
        with transaction(conn):
            pid2 = selfcode.store(conn, selfcode.build(self.root, "app/core/voice.py", edits), "De novo", "x", None, at(3))
        self.voice.write_bytes(original + b"# mexi\r\n")
        with self.assertRaises(selfcode.SelfCodeError):
            with transaction(conn):
                selfcode.apply(conn, self.root, self.backups, pid2, at(4))

    def _db(self):
        from app.database.migrator import initialize_database
        db = self.root / "data" / "nym.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        initialize_database(db)
        return db


class ProposeToolTests(DbTestCase):
    def test_propose_tool_stores_a_proposal_and_never_applies(self):
        root = Path(self.tmp.name) / "proj"
        (root / "app" / "core").mkdir(parents=True)
        target = root / "app" / "core" / "voice.py"
        target.write_text('RULES = (\n    "frases curtas",\n)\n')
        settings = self.make_settings(code_root=root)
        reply = json.dumps({"why": "Quero perguntar menos.", "edits": [
            {"find": '    "frases curtas",', "replace": '    "frases curtas",\n    "uma pergunta por vez",'}]})
        tool = ToolRegistry().get("propose_improvement")
        out = tool.run(ToolContext(settings=settings, provider=ScriptedProvider([reply]), clock=clock_at(0)),
                       tool.validate({"path": "app/core/voice.py", "title": "Menos perguntas", "instructions": "perguntar menos"}))
        self.assertIn("NÃO foi aplicada", out)
        self.assertNotIn("uma pergunta", target.read_text())
        svc = AtelierService(settings)
        p = svc.proposals()[0]
        self.assertEqual((p["status"], p["file"], p["why"]), ("proposed", "app/core/voice.py", "Quero perguntar menos."))
        svc.decide(p["id"], "apply")
        self.assertIn("uma pergunta por vez", target.read_text())
        with self.assertRaises(AtelierError):
            svc.decide(p["id"], "apply")
        svc.decide(p["id"], "revert")
        self.assertNotIn("uma pergunta", target.read_text())

    def test_no_tool_can_apply_code(self):
        names = ToolRegistry().names()
        self.assertFalse([n for n in names if "apply" in n or "patch" in n])


class LessonTests(DbTestCase):
    def conn(self):
        c = connect(self.settings.db_path)
        self.addCleanup(c.close)
        return c

    def test_dedupe_relevance_score_and_retirement(self):
        c = self.conn()
        with transaction(c):
            a = lessons.add(c, "task", "Buscar em inglês traz mais fontes sobre jogos indie", ["web_search"], {"goal": 1}, at(0))
            b = lessons.add(c, "task", "Buscar em inglês traz mais fontes sobre jogos indie!", ["web_search"], {"goal": 2}, at(1))
        self.assertTrue(a["new"])
        self.assertEqual((b["id"], b["new"]), (a["id"], False))
        self.assertEqual(lessons.relevant(c, "task", "pesquisar jogos indie novos")[0]["id"], a["id"])
        self.assertEqual(lessons.relevant(c, "task", "fazer um bolo de cenoura"), [])
        for i in range(3):
            with transaction(c):
                lessons.mark_used(c, [a["id"]], "goal", 10 + i, at(2))
                lessons.settle(c, "goal", 10 + i, False, at(3))
        row = rows(self.settings, "SELECT * FROM lessons WHERE id = ?", (a["id"],))[0]
        self.assertEqual((row["losses"], row["uses"], row["status"]), (3, 3, "retired"))
        with transaction(c):                                # resultado só conta uma vez
            self.assertEqual(lessons.settle(c, "goal", 10, True, at(4)), [])

    def test_manager_learns_from_evaluation_and_scores_the_lesson(self):
        provider = ScriptedProvider([
            plan(("hora", "datetime_now", {})),
            json.dumps({"done": True, "summary": "Vi a hora.", "steps": [],
                        "lesson": {"text": "Para saber o horário dele, datetime_now resolve em um passo só", "steps": [1]}}),
            plan(("hora", "datetime_now", {})),
            json.dumps({"done": True, "summary": "Vi a hora de novo.", "steps": [], "lesson": None}),
        ])
        m = TaskManager(self.settings, provider, ToolRegistry(), MemoryService(self.settings), clock=clock_at(0), tz=timezone.utc)
        m.create_goal("Ver o horário dele agora")
        run(m)
        les = lessons.listing(self.conn())
        self.assertEqual(len(les), 1)
        self.assertEqual(les[0]["tools"], ["datetime_now"])
        g2 = m.create_goal("Conferir o horário dele de novo")
        run(m)
        plan_prompt = [c for c in provider.calls if "planejando" in c[0].content][-1][1].content
        self.assertIn("datetime_now resolve", plan_prompt)            # a lição entrou no planejamento
        self.assertIn("deu certo assim", plan_prompt)                 # e a receita do objetivo parecido
        self.assertEqual(m.get_goal(g2["id"])["status"], "done")
        self.assertEqual(lessons.listing(self.conn())[0]["wins"], 1)  # e ganhou ponto porque deu certo

    def test_lesson_without_real_step_is_dropped(self):
        provider = ScriptedProvider([
            plan(("hora", "datetime_now", {})),
            json.dumps({"done": True, "summary": "ok", "steps": [], "lesson": {"text": "Uma lição inventada sem evidência nenhuma", "steps": [9]}}),
        ])
        m = TaskManager(self.settings, provider, ToolRegistry(), clock=clock_at(0), tz=timezone.utc)
        m.create_goal("Ver o horário dele agora")
        run(m)
        self.assertEqual(lessons.listing(self.conn()), [])


class FeedbackTests(DbTestCase):
    def test_reactions_score_chat_lessons_and_become_new_lessons(self):
        ids = seed_chat(self.settings, [("user", "fui mal na prova", 0), ("assistant", "Que pena! Quer conversar sobre isso? " * 3, 1),
                                        ("user", "não", 2), ("assistant", "Tá bom.", 3), ("user", "ok", 4), ("assistant", "Valeu.", 5)])
        c = connect(self.settings.db_path)
        self.addCleanup(c.close)
        with transaction(c):
            lid = lessons.add(c, "chat", "Responda curto quando ele estiver desanimado", [], {"feedback": []}, at(0))["id"]
            lessons.mark_used(c, [lid], "message", ids[1], at(1))
        svc = AtelierService(self.settings)
        with self.assertRaises(AtelierError):
            svc.react("message", ids[0], 1)                     # mensagem do usuário não recebe reação
        svc.react("message", ids[1], -1, "textão demais")
        self.assertEqual(rows(self.settings, "SELECT losses FROM lessons WHERE id = ?", (lid,))[0]["losses"], 1)
        svc.react("message", ids[1], 1)                         # mudou de ideia: o ponto troca de lado
        r = rows(self.settings, "SELECT wins, losses FROM lessons WHERE id = ?", (lid,))[0]
        self.assertEqual((r["wins"], r["losses"]), (1, 0))
        svc.react("message", ids[3], 1)
        svc.react("message", ids[5], 1)
        provider = ScriptedProvider([json.dumps({"lessons": [
            {"text": "Quando ele responde só 'não', não insista no assunto", "feedback": [1, 2]},
            {"text": "Lição sem reação real que a sustente", "feedback": [999]}]})])
        m = TaskManager(self.settings, provider, ToolRegistry(), clock=clock_at(60), tz=timezone.utc)
        res = m._learning_step()
        self.assertEqual(len(res["lessons"]), 1)
        self.assertIn("não insista", lessons.listing(c)[0]["text"])
        self.assertIsNone(m._learning_step())                   # reações já usadas não são reprocessadas
        hist = __import__("app.core.chat_service", fromlist=["ChatService"]).ChatService(
            self.settings, ScriptedProvider([])).get_history()["messages"]
        self.assertEqual({m_["id"]: m_["reaction"] for m_ in hist if m_["role"] == "assistant"}[ids[1]], 1)

    def test_chat_context_gets_lessons_and_creations_and_marks_use(self):
        seed_chat(self.settings, [("user", "oi", 0), ("assistant", "oi!", 1)])
        c = connect(self.settings.db_path)
        self.addCleanup(c.close)
        with transaction(c):
            lid = lessons.add(c, "chat", "Quando ele diz oi, responda com uma pergunta sobre o dia dele", [], {}, at(0))["id"]
            store.save(c, "drawing", "Céu dele", "céu", "<svg/>", at(0))
        from app.core.chat_service import ChatService
        provider = ScriptedProvider(["E aí, como foi o dia?"])
        res = ChatService(self.settings, provider, clock=clock_at(10), tz=timezone.utc).send_message("oi de novo")
        ctx = provider.calls[0][-1].content
        self.assertIn("pergunta sobre o dia dele", ctx)
        self.assertIn('desenho "Céu dele"', ctx)
        used = rows(self.settings, "SELECT * FROM lesson_uses WHERE target = 'message'")
        self.assertEqual([(u["lesson_id"], u["target_id"]) for u in used], [(lid, res.assistant_message_id)])


if __name__ == "__main__":
    unittest.main()
