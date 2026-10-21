"""Fase 6.1: notas com conteúdo de verdade, sonhos que não se repetem e vontades sem reformulações do mesmo tema."""
import json
from datetime import timezone

from app.database.connection import connect, transaction
from app.dreams.associations import build_messages
from app.dreams.engine import DreamEngine, MAX_OLD_MEMORIES, previous_dreams, select_memories
from app.core.identity import IdentityService
from app.memory.service import MemoryService
from app.tasks import desires
from app.tasks.manager import TaskManager
from app.tools.base import ToolContext, ToolError
from app.tools.builtin import looks_unfinished
from app.tools.registry import ToolRegistry
from tests.dreamtools import add_mem, dream_reply, rows
from tests.fakes import DbTestCase, ScriptedProvider
from tests.memtools import clock_at


def plan(*steps):
    return json.dumps({"steps": [{"title": t, "tool": tool, "args": args} for t, tool, args in steps]})


class NoteTests(DbTestCase):
    def test_placeholders_are_refused(self):
        for text in ("[Inserir memórias e análise aqui]", "Conteúdo a ser preenchido com base nas memórias",
                     "Significado de Desejo - [data e hora]", "Soluções: [inserir soluções]"):
            self.assertTrue(looks_unfinished(text), text)
        self.assertFalse(looks_unfinished("O DaVinci Resolve é gratuito [fonte: wikipedia]"))
        tool = ToolRegistry().get("write_note")
        with self.assertRaises(ToolError):
            tool.run(ToolContext(settings=self.settings), tool.validate({"title": "x", "content": "[inserir análise aqui]"}))

    def test_compose_note_writes_from_real_results_and_updates(self):
        add_mem(self.settings, "O usuário quer começar a editar vídeos para o canal")
        notes = ["## Ideias de vídeo\n- Vídeo sobre editar vídeos para o canal (lembrança #1).",
                 "## Ideias de vídeo\n- Vídeo sobre editar vídeos para o canal (lembrança #1).\n- Usar o DaVinci Resolve."]
        provider = ScriptedProvider([
            plan(("procurar", "memory_search", {"query": "vídeo canal"}),
                 ("escrever", "compose_note", {"title": "Ideias de vídeo", "instructions": "listar as ideias de vídeo dele"})),
            notes[0],                                                                     # o texto escrito na hora
            json.dumps({"done": True, "summary": "Nota escrita.", "steps": []}),
        ])
        m = TaskManager(self.settings, provider, ToolRegistry(), MemoryService(self.settings), clock=clock_at(0), tz=timezone.utc)
        m.set_permission("compose_note", "auto")
        goal = m.create_goal("Juntar as ideias de vídeo dele numa nota")
        for _ in range(20):
            if m.tick() is None:
                break
        path = self.settings.data_dir / "notes" / "ideias-de-video.md"
        text = path.read_text(encoding="utf-8")
        self.assertIn("Vídeo sobre editar vídeos", text)
        self.assertEqual(m.get_goal(goal["id"])["status"], "done")
        compose_call = provider.calls[1]
        self.assertIn("O usuário quer começar a editar vídeos", compose_call[1].content)   # material = resultado real do passo 1
        # Atualizar: mesmo título, o modelo recebe a versão anterior e a nota é reescrita (não fica uma segunda nota).
        provider.replies = [plan(("atualizar", "compose_note", {"title": "Ideias de vídeo", "instructions": "acrescentar o editor"})),
                            notes[1], json.dumps({"done": True, "summary": "Atualizada.", "steps": []})]
        m.create_goal("Atualizar a nota de ideias de vídeo com o editor")
        for _ in range(20):
            if m.tick() is None:
                break
        self.assertIn("DaVinci", path.read_text(encoding="utf-8"))
        self.assertEqual(len(list((self.settings.data_dir / "notes").glob("*.md"))), 1)

    def test_compose_note_refuses_without_material(self):
        provider = ScriptedProvider([plan(("escrever", "compose_note", {"title": "Vazia", "instructions": "algo"})),
                                     json.dumps({"done": False, "summary": "Não deu.", "steps": []})])
        m = TaskManager(self.settings, provider, ToolRegistry(), clock=clock_at(0), tz=timezone.utc)
        m.set_permission("compose_note", "auto")
        goal = m.create_goal("Escrever uma nota sem pesquisar nada")
        for _ in range(10):
            if m.tick() is None:
                break
        task = m.get_goal(goal["id"])["tasks"][0]
        self.assertEqual(task["status"], "failed")
        self.assertFalse((self.settings.data_dir / "notes" / "vazia.md").exists())


class DreamVarietyTests(DbTestCase):
    def test_old_memories_rotate_between_dreams(self):
        for i in range(30):
            add_mem(self.settings, f"Lembrança antiga número {i} sobre assunto {i}", importance=0.5 + (i % 5) / 10, minutes=-100)
        engine = DreamEngine(self.settings, ScriptedProvider([dream_reply()] * 5), IdentityService(self.settings), clock=clock_at(0))
        seen = []
        for _ in range(3):
            conn = connect(self.settings.db_path)
            picked, _ = select_memories(conn, 30)
            conn.close()
            seen.append({m["id"] for m in picked})
            engine.run_cycle("manual")
        self.assertLessEqual(len(seen[1]), MAX_OLD_MEMORIES + 1)
        self.assertLess(len(seen[1] & seen[2]), len(seen[2]))                 # não são as mesmas memórias de novo

    def test_prompt_shows_previous_dreams_and_marks_new_memories(self):
        add_mem(self.settings, "O usuário gosta de Digimon")
        engine = DreamEngine(self.settings, ScriptedProvider([dream_reply(review="Sonhei com Digimon e o canal.")]),
                             IdentityService(self.settings), clock=clock_at(0))
        engine.run_cycle("manual")
        add_mem(self.settings, "O usuário comprou um microfone", minutes=600)
        conn = connect(self.settings.db_path)
        picked, _ = select_memories(conn, 30)
        msgs = build_messages(picked, [], previous_dreams(conn))
        conn.close()
        self.assertIn("Sonhei com Digimon e o canal.", msgs[1].content)
        self.assertIn("NÃO repita o que já foi sonhado", msgs[0].content)


class DesireVarietyTests(DbTestCase):
    def test_reworded_same_theme_is_dropped_but_concrete_idea_passes(self):
        mem = add_mem(self.settings, "O usuário quer que eu tenha desejos próprios")
        ctx = {"memories": [{"id": mem}], "dream_items": [], "cycles": [],
               "existing_goals": ["Escrever uma nota explorando a relação entre a percepção de valor de Nym e a sua relação com João",
                                  "Escrever uma nota detalhada sobre o significado de desejo para Nym"]}
        raw = json.dumps({"goals": [
            {"description": "Analisar a relação entre valor e autonomia de Nym com João", "why": "x", "refs": {"memories": [mem]}},
            {"description": "Escrever uma nota sobre o que desejo significa para Nym", "why": "x", "refs": {"memories": [mem]}},
            {"description": "Pesquisar editores de vídeo gratuitos para o canal do João", "why": "ele quer editar", "refs": {"memories": [mem]}},
        ]})
        result = desires.parse_desires(raw, ctx, 3)
        self.assertEqual([d.description for d in result.desires], ["Pesquisar editores de vídeo gratuitos para o canal do João"])
        self.assertEqual(len(result.dropped), 2)

    def test_unfinished_notes_are_shown_to_him(self):
        notes = self.settings.data_dir / "notes"
        notes.mkdir()
        (notes / "desejo.md").write_text("# Desejo\n\nConteúdo a ser preenchido.", encoding="utf-8")
        overview = desires.notes_overview(notes)
        self.assertTrue(overview[0]["unfinished"])
        ctx = {"identity": [], "questions": [], "cycles": [], "dream_items": [], "memories": [], "existing_goals": [], "notes": overview}
        text = desires.build_messages(ctx, ToolRegistry(), 2)[1].content
        self.assertIn("Desejo [INACABADA]", text)
