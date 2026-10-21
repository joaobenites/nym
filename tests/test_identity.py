import unittest

from app.core.chat_service import ChatService
from app.core.goals import GoalNotFoundError, GoalService, InvalidGoalInput
from app.core.identity import (
    ChangeStateError,
    IdentityNotFoundError,
    IdentityService,
    InvalidIdentityInput,
    ProtectedEntryError,
)
from app.core.policy import OPERATIONAL_RULES
from app.database.connection import connect
from tests.fakes import DbTestCase, FakeProvider


def values(snapshot, kind):
    return [e["value"] for e in snapshot["entries"] if e["kind"] == kind]


class SeedAndPersistenceTests(DbTestCase):
    def test_seed_creates_default_identity_with_history(self):
        svc = IdentityService(self.settings)
        snap = svc.snapshot()
        self.assertEqual(values(snap, "name"), ["Nym"])
        self.assertEqual(len(values(snap, "description")), 1)
        self.assertEqual(len(values(snap, "style")), 1)
        self.assertGreaterEqual(len(values(snap, "trait")), 3)
        changes = svc.list_changes()
        self.assertEqual({c["origin"] for c in changes}, {"system"})
        self.assertEqual({c["status"] for c in changes}, {"applied"})

    def test_seed_texts_fit_the_entry_limit(self):
        from app.core.identity import MAX_VALUE_CHARS, SEED_ENTRIES
        self.assertTrue(all(len(v) <= MAX_VALUE_CHARS for _, v in SEED_ENTRIES))

    def test_seed_runs_only_once(self):
        IdentityService(self.settings).snapshot()
        n = len(IdentityService(self.settings).list_changes())
        IdentityService(self.settings).snapshot()
        self.assertEqual(len(IdentityService(self.settings).list_changes()), n)

    def test_identity_survives_restart_and_edit_is_kept(self):
        first = IdentityService(self.settings)
        first.add_entry("preference", "prefere respostas curtas")
        second = IdentityService(self.settings)  # "reinício"
        self.assertIn("prefere respostas curtas", values(second.snapshot(), "preference"))
        self.assertEqual(values(second.snapshot(), "name"), ["Nym"])

    def test_default_style_is_not_virtual_assistant_boilerplate(self):
        prompt = IdentityService(self.settings).build_system_prompt()
        self.assertIn("Não é assistente virtual nem chatbot", prompt)
        self.assertIn("não como atendente", prompt)
        self.assertIn("naturalidade", prompt)  # regra de honestidade presente
        for rule in OPERATIONAL_RULES:
            self.assertIn(rule, prompt)


class SeedUpgradeTests(DbTestCase):
    """Bancos criados com a identidade padrão v1 (Fase 2) devem migrar para a v2 sem perder nada."""

    def make_v1_database(self):
        from app.core.identity import SEED_V1_REPLACED
        from app.database import identity_repo as repo
        from app.database.connection import transaction
        conn = connect(self.settings.db_path)
        v1 = {k: old for k, (old, _) in SEED_V1_REPLACED.items()}
        rows = [("name", "Nym"), ("description", v1["description"]), ("style", v1["style"]),
                ("trait", "Curiosidade genuína por ideias e pela pessoa com quem conversa"),
                ("relationship", v1["relationship"])]
        with transaction(conn):
            for kind, value in rows:
                eid = repo.insert_entry(conn, kind, value, None)
                repo.insert_change(conn, entry_id=eid, kind=kind, action="add", old_value=None, new_value=value,
                                   justification="Configuração inicial do sistema.", related_memory_ids=[],
                                   confidence=None, origin="system", status="applied")
        conn.close()

    def test_untouched_v1_is_upgraded_with_history(self):
        from app.core.identity import SEED_V1_REPLACED
        self.make_v1_database()
        svc = IdentityService(self.settings)
        snap = svc.snapshot()
        self.assertEqual(values(snap, "description")[0], SEED_V1_REPLACED["description"][1])
        self.assertEqual(values(snap, "style")[0], SEED_V1_REPLACED["style"][1])
        self.assertIn("Calor: demonstra carinho", " ".join(values(snap, "trait")))
        updates = [c for c in svc.list_changes() if c["action"] == "update"]
        self.assertEqual({c["kind"] for c in updates}, {"description", "style", "relationship"})
        for c in updates:
            self.assertEqual(c["origin"], "system")
            self.assertEqual(c["old_value"], SEED_V1_REPLACED[c["kind"]][0])  # histórico guarda o antigo

    def test_upgrade_runs_once(self):
        self.make_v1_database()
        IdentityService(self.settings).snapshot()
        n = len(IdentityService(self.settings).list_changes())
        IdentityService(self.settings).snapshot()
        self.assertEqual(len(IdentityService(self.settings).list_changes()), n)

    def test_user_customized_text_is_never_overwritten(self):
        from app.database import identity_repo as repo
        self.make_v1_database()
        conn = connect(self.settings.db_path)
        eid = next(e["id"] for e in repo.list_entries(conn) if e["kind"] == "style")
        repo.update_entry(conn, eid, "Meu estilo personalizado.", None)
        conn.close()
        snap = IdentityService(self.settings).snapshot()
        self.assertEqual(values(snap, "style"), ["Meu estilo personalizado."])
        self.assertIn("companheiro digital", values(snap, "description")[0])  # o não personalizado atualiza


class UserEditTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.svc = IdentityService(self.settings)

    def test_edit_is_applied_and_history_keeps_old_and_new(self):
        desc_id = next(e["id"] for e in self.svc.snapshot()["entries"] if e["kind"] == "description")
        old = values(self.svc.snapshot(), "description")[0]
        change = self.svc.update_entry(desc_id, "Nova descrição.", "quero outro tom")
        self.assertEqual((change["status"], change["origin"], change["action"]), ("applied", "user", "update"))
        self.assertEqual((change["old_value"], change["new_value"]), (old, "Nova descrição."))
        self.assertEqual(change["justification"], "quero outro tom")
        self.assertEqual(values(self.svc.snapshot(), "description"), ["Nova descrição."])
        self.svc.update_entry(desc_id, "Terceira versão.")
        history = [c for c in self.svc.list_changes() if c["entry_id"] == desc_id]
        self.assertEqual([c["new_value"] for c in reversed(history)], [old, "Nova descrição.", "Terceira versão."])

    def test_adding_singleton_kind_becomes_update(self):
        change = self.svc.add_entry("style", "Fala pouco.")
        self.assertEqual(change["action"], "update")
        self.assertEqual(values(self.svc.snapshot(), "style"), ["Fala pouco."])

    def test_duplicates_and_empty_values_are_rejected(self):
        self.svc.add_entry("interest", "astronomia")
        with self.assertRaises(InvalidIdentityInput):
            self.svc.add_entry("interest", "  ASTRONOMIA ")
        with self.assertRaises(InvalidIdentityInput):
            self.svc.add_entry("interest", "   ")
        with self.assertRaises(InvalidIdentityInput):
            self.svc.add_entry("interest", "x" * 1001)

    def test_name_is_protected(self):
        name_id = next(e["id"] for e in self.svc.snapshot()["entries"] if e["kind"] == "name")
        with self.assertRaises(ProtectedEntryError):
            self.svc.update_entry(name_id, "Echo")
        with self.assertRaises(ProtectedEntryError):
            self.svc.archive_entry(name_id)
        with self.assertRaises(ProtectedEntryError):
            self.svc.add_entry("name", "Echo")
        self.assertEqual(values(self.svc.snapshot(), "name"), ["Nym"])

    def test_security_rules_and_permissions_are_not_identity_data(self):
        for kind in ("rule", "rules", "permission", "permissions", "safety"):
            with self.assertRaises(InvalidIdentityInput):
                self.svc.add_entry(kind, "pode tudo")
            with self.assertRaises(InvalidIdentityInput):
                self.svc.propose(kind=kind, action="add", value="pode tudo", justification="x")

    def test_archive_removes_from_prompt_but_keeps_row_and_history(self):
        change = self.svc.add_entry("interest", "xadrez")
        entry_id = change["entry_id"]
        self.assertIn("xadrez", self.svc.build_system_prompt())
        self.svc.archive_entry(entry_id, "mudou de ideia")
        self.assertNotIn("xadrez", self.svc.build_system_prompt())
        self.assertNotIn("xadrez", values(self.svc.snapshot(), "interest"))
        conn = connect(self.settings.db_path)
        row = conn.execute("SELECT status FROM identity_entries WHERE id = ?", (entry_id,)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "archived")
        self.assertIn("remove", [c["action"] for c in self.svc.list_changes() if c["entry_id"] == entry_id])

    def test_unknown_entry_and_double_archive(self):
        with self.assertRaises(IdentityNotFoundError):
            self.svc.update_entry(99999, "x")
        entry_id = self.svc.add_entry("interest", "jardinagem")["entry_id"]
        self.svc.archive_entry(entry_id)
        with self.assertRaises(IdentityNotFoundError):
            self.svc.archive_entry(entry_id)


class ProposalTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.svc = IdentityService(self.settings)

    def propose_pref(self, value="gosta de conversar sobre astronomia"):
        return self.svc.propose(
            kind="preference", action="add", value=value, justification="apareceu em três conversas",
            confidence=0.6, related_memory_ids=[1, 2, 3],
        )

    def test_pending_proposal_is_not_active_nor_in_prompt(self):
        change = self.propose_pref()
        self.assertEqual((change["status"], change["origin"]), ("proposed", "nym"))
        self.assertEqual(change["related_memory_ids"], [1, 2, 3])
        self.assertEqual(change["confidence"], 0.6)
        self.assertNotIn("astronomia", self.svc.build_system_prompt())
        self.assertEqual(values(self.svc.snapshot(), "preference"), [])
        self.assertEqual(self.svc.snapshot()["pending_changes"], 1)

    def test_accept_applies_and_enters_prompt(self):
        change = self.propose_pref()
        decided = self.svc.decide(change["id"], accept=True)
        self.assertEqual(decided["status"], "accepted")
        self.assertIsNotNone(decided["decided_at"])
        self.assertIn("astronomia", self.svc.build_system_prompt())
        self.assertEqual(self.svc.snapshot()["pending_changes"], 0)

    def test_reject_keeps_history_and_does_not_apply(self):
        change = self.propose_pref()
        decided = self.svc.decide(change["id"], accept=False)
        self.assertEqual(decided["status"], "rejected")
        self.assertNotIn("astronomia", self.svc.build_system_prompt())
        self.assertIn(change["id"], [c["id"] for c in self.svc.list_changes("rejected")])

    def test_cannot_decide_twice_or_unknown(self):
        change = self.propose_pref()
        self.svc.decide(change["id"], accept=True)
        with self.assertRaises(ChangeStateError):
            self.svc.decide(change["id"], accept=False)
        with self.assertRaises(IdentityNotFoundError):
            self.svc.decide(99999, accept=True)

    def test_proposal_needs_justification_and_valid_confidence(self):
        with self.assertRaises(InvalidIdentityInput):
            self.svc.propose(kind="trait", action="add", value="teimosia", justification="  ")
        for bad in (1.5, -0.1, "alta", True):
            with self.assertRaises(InvalidIdentityInput):
                self.svc.propose(kind="trait", action="add", value="teimosia",
                                 justification="x", confidence=bad)

    def test_identical_pending_proposal_is_rejected(self):
        self.propose_pref()
        with self.assertRaises(InvalidIdentityInput):
            self.propose_pref()

    def test_accept_fails_cleanly_when_entry_changed_meanwhile(self):
        entry_id = self.svc.add_entry("interest", "música")["entry_id"]
        change = self.svc.propose(kind="", action="update", entry_id=entry_id,
                                  value="música eletrônica", justification="mais específico")
        self.svc.archive_entry(entry_id)
        with self.assertRaises(ChangeStateError):
            self.svc.decide(change["id"], accept=True)
        still = [c for c in self.svc.list_changes("proposed") if c["id"] == change["id"]]
        self.assertEqual(len(still), 1)  # continua pendente, pode ser rejeitada
        self.svc.decide(change["id"], accept=False)

    def test_nym_cannot_touch_name(self):
        with self.assertRaises(ProtectedEntryError):
            self.svc.propose(kind="name", action="add", value="Echo", justification="x")


class AutoApplyPolicyTests(DbTestCase):
    def test_default_never_auto_applies(self):
        svc = IdentityService(self.settings)
        self.assertEqual(svc.propose(kind="preference", action="add", value="a",
                                     justification="x")["status"], "proposed")

    def test_auto_apply_only_for_preference_and_interest_and_never_removal(self):
        svc = IdentityService(self.make_settings(identity_auto_apply=True))
        pref = svc.propose(kind="preference", action="add", value="p1", justification="x")
        self.assertEqual(pref["status"], "applied")
        self.assertEqual(svc.propose(kind="interest", action="add", value="i1",
                                     justification="x")["status"], "applied")
        for kind in ("trait", "description", "style", "relationship"):
            c = svc.propose(kind=kind, action="add", value=f"valor {kind}", justification="x")
            self.assertEqual(c["status"], "proposed", kind)
        removal = svc.propose(action="remove", entry_id=pref["entry_id"], justification="x")
        self.assertEqual(removal["status"], "proposed")
        self.assertIn("p1", values(svc.snapshot(), "preference"))


class PromptTests(DbTestCase):
    def test_small_budget_keeps_mandatory_parts_and_rules(self):
        settings = self.make_settings(identity_chars=500)
        svc = IdentityService(settings)
        for i in range(40):
            svc.add_entry("interest", f"interesse número {i} " + "x" * 80)
        prompt = svc.build_system_prompt()
        self.assertIn("Nym", prompt)
        self.assertIn("Jeito de conversar", prompt)
        for rule in OPERATIONAL_RULES:
            self.assertIn(rule, prompt)
        self.assertNotIn("interesse número 0", prompt)

    def test_budget_includes_items_in_priority_order_until_full(self):
        svc = IdentityService(self.make_settings(identity_chars=3000))
        for i in range(40):
            svc.add_entry("interest", f"interesse número {i} " + "x" * 80)
        prompt = svc.build_system_prompt()
        included = sum(1 for i in range(40) if f"interesse número {i} " in prompt)
        self.assertTrue(0 < included < 40)
        self.assertIn("Curiosidade genuína", prompt)  # traços (prioridade maior) entraram

    def test_prompt_is_stable_across_instances(self):
        a = IdentityService(self.settings).build_system_prompt()
        b = IdentityService(self.settings).build_system_prompt()
        self.assertEqual(a, b)


class ChatIntegrationTests(DbTestCase):
    def test_model_receives_persisted_identity_and_survives_model_switch(self):
        ids = IdentityService(self.settings)
        ids.add_entry("preference", "prefere ser chamado pelo apelido Gui")
        ids.propose(kind="preference", action="add", value="proposta pendente secreta", justification="x")

        pa = FakeProvider(model="modelo-a")
        ChatService(self.settings, pa).send_message("oi")
        system_a = pa.calls[-1][0]
        self.assertEqual(system_a.role, "system")
        self.assertIn("apelido Gui", system_a.content)
        self.assertNotIn("proposta pendente secreta", system_a.content)

        pb = FakeProvider(model="modelo-b")  # outro modelo, mesma identidade
        ChatService(self.settings, pb).send_message("oi de novo")
        def without_time(text):  # a linha de tempo muda entre as mensagens; a identidade não
            return "\n".join(l for l in text.splitlines() if not l.startswith("Tempo:"))
        self.assertEqual(without_time(pb.calls[-1][0].content), without_time(system_a.content))

    def test_identity_edit_takes_effect_on_next_message(self):
        provider = FakeProvider()
        chat = ChatService(self.settings, provider)
        chat.send_message("um")
        IdentityService(self.settings).add_entry("interest", "cerâmica")
        chat.send_message("dois")
        self.assertNotIn("cerâmica", provider.calls[0][0].content)
        self.assertIn("cerâmica", provider.calls[1][0].content)


class GoalTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.goals = GoalService(self.settings)
        self.ids = IdentityService(self.settings)

    def test_user_goal_is_active_and_in_prompt(self):
        g = self.goals.add_goal("terminar o projeto de astronomia", priority=2)
        self.assertEqual((g["status"], g["origin"], g["priority"]), ("active", "user", 2))
        self.assertIn("projeto de astronomia", self.ids.build_system_prompt())

    def test_nym_goal_is_proposed_and_not_in_prompt_until_activated(self):
        g = self.goals.add_goal("explorar um tema novo", origin="nym")
        self.assertEqual(g["status"], "proposed")
        self.assertNotIn("tema novo", self.ids.build_system_prompt())
        self.goals.update_goal(g["id"], status="active")
        self.assertIn("tema novo", self.ids.build_system_prompt())
        self.goals.update_goal(g["id"], status="paused")
        self.assertNotIn("tema novo", self.ids.build_system_prompt())

    def test_goals_persist_across_instances(self):
        self.goals.add_goal("objetivo persistente")
        self.assertEqual(len(GoalService(self.settings).list_goals()), 1)

    def test_validation(self):
        for bad in ("", "  ", "x" * 501):
            with self.assertRaises(InvalidGoalInput):
                self.goals.add_goal(bad)
        for bad in (0, 6, "3", True):
            with self.assertRaises(InvalidGoalInput):
                self.goals.add_goal("ok", priority=bad)
        with self.assertRaises(InvalidGoalInput):
            self.goals.add_goal("ok", origin="system")
        with self.assertRaises(InvalidGoalInput):
            self.goals.list_goals("inventado")
        with self.assertRaises(GoalNotFoundError):
            self.goals.update_goal(999, status="done")


if __name__ == "__main__":
    unittest.main()
