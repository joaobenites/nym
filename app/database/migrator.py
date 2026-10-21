"""Migrações SQL versionadas.

Arquivos em `migrations/` com nome `NNN_descricao.sql`, aplicados em ordem.
Cada migração roda numa transação junto com seu registro em
`schema_migrations`: ou tudo é aplicado, ou nada. Os arquivos NÃO devem conter
BEGIN/COMMIT. Migrações já aplicadas nunca são reexecutadas, então os dados
existentes são preservados em atualizações.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.database.connection import connect

log = logging.getLogger("nym.database")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME_RE = re.compile(r"^(\d{3,})_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    pass


def discover(migrations_dir: Path = MIGRATIONS_DIR) -> list[tuple[int, str, Path]]:
    found: dict[int, tuple[int, str, Path]] = {}
    for path in sorted(Path(migrations_dir).glob("*.sql")):
        match = _NAME_RE.match(path.name)
        if not match:
            raise MigrationError(f"Nome de migração inválido: {path.name}")
        version = int(match.group(1))
        if version in found:
            raise MigrationError(f"Versão de migração duplicada: {version}")
        found[version] = (version, path.name, path)
    return [found[v] for v in sorted(found)]


def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Aplica as migrações pendentes. Retorna os nomes aplicados nesta chamada."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    applied_versions = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied_now: list[str] = []
    for version, name, path in discover(migrations_dir):
        if version in applied_versions:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript("BEGIN IMMEDIATE;\n" + sql + "\n;")
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, datetime.now(timezone.utc).isoformat(timespec="milliseconds")),
            )
            conn.execute("COMMIT")
        except Exception as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(f"Falha ao aplicar {name}: {exc}") from exc
        log.info("Migração aplicada: %s", name)
        applied_now.append(name)
    return applied_now


def initialize_database(db_path: str | Path, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    conn = connect(db_path)
    try:
        return apply_migrations(conn, migrations_dir)
    finally:
        conn.close()
