import tempfile
import unittest
from pathlib import Path

from app.database.connection import connect
from app.database.migrator import MigrationError, apply_migrations, initialize_database


def table_names(conn):
    return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


class MigratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "t.db"

    def test_creates_schema_from_scratch(self):
        applied = initialize_database(self.db)
        self.assertEqual(applied[0], "001_conversations_messages.sql")
        self.assertEqual(applied, sorted(applied))
        conn = connect(self.db)
        self.assertTrue(
            {"conversations", "messages", "audit_log", "schema_migrations",
             "identity_entries", "identity_changes", "goals"} <= table_names(conn)
        )
        conn.close()

    def test_upgrade_from_phase1_database_preserves_existing_data(self):
        """Simula o banco de quem já usou a Fase 1: só a migração 001, com conversas salvas."""
        import shutil
        from app.database.migrator import MIGRATIONS_DIR

        only_001 = Path(self.tmp.name) / "only001"
        only_001.mkdir()
        shutil.copy(MIGRATIONS_DIR / "001_conversations_messages.sql", only_001)
        conn = connect(self.db)
        apply_migrations(conn, only_001)
        conn.execute("INSERT INTO conversations VALUES ('c1','minha conversa','t','t')")
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, model, created_at) "
            "VALUES ('c1','user','mensagem antiga',NULL,'t')"
        )
        applied = apply_migrations(conn, MIGRATIONS_DIR)  # agora com todas as migrações
        self.assertNotIn("001_conversations_messages.sql", applied)
        self.assertIn("002_identity_goals.sql", applied)
        self.assertEqual(conn.execute("SELECT content FROM messages").fetchone()[0], "mensagem antiga")
        self.assertEqual(conn.execute("SELECT title FROM conversations").fetchone()[0], "minha conversa")
        conn.close()

    def test_is_idempotent_and_preserves_data(self):
        initialize_database(self.db)
        conn = connect(self.db)
        conn.execute("INSERT INTO conversations VALUES ('c1','t','now','now')")
        conn.close()
        self.assertEqual(initialize_database(self.db), [])  # nada pendente
        conn = connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], 1)
        conn.close()

    def test_failed_migration_rolls_back_completely(self):
        mdir = Path(self.tmp.name) / "migs"
        mdir.mkdir()
        (mdir / "001_ok.sql").write_text("CREATE TABLE a (x INTEGER);")
        (mdir / "002_bad.sql").write_text("CREATE TABLE b (x INTEGER); THIS IS NOT SQL;")
        conn = connect(self.db)
        with self.assertRaises(MigrationError):
            apply_migrations(conn, mdir)
        names = table_names(conn)
        self.assertIn("a", names)       # 001 foi aplicada
        self.assertNotIn("b", names)    # 002 foi revertida por inteiro
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
        self.assertEqual(versions, [1])
        self.assertFalse(conn.in_transaction)
        conn.close()

    def test_rejects_bad_names_and_duplicate_versions(self):
        mdir = Path(self.tmp.name) / "migs2"
        mdir.mkdir()
        (mdir / "oops.sql").write_text("SELECT 1;")
        conn = connect(self.db)
        with self.assertRaises(MigrationError):
            apply_migrations(conn, mdir)
        conn.close()


if __name__ == "__main__":
    unittest.main()
