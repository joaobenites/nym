-- Identidade persistente: o estado atual fica em identity_entries;
-- toda alteração fica registrada em identity_changes (nada é sobrescrito sem rastro).
CREATE TABLE identity_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN
        ('name','description','style','trait','preference','interest','relationship','unknown_aspect')),
    value TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','archived')),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_identity_entries_kind ON identity_entries(kind, status);

CREATE TABLE identity_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER REFERENCES identity_entries(id),
    kind TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('add','update','remove')),
    old_value TEXT,
    new_value TEXT,
    justification TEXT NOT NULL,
    related_memory_ids TEXT NOT NULL DEFAULT '[]',
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    origin TEXT NOT NULL CHECK (origin IN ('user','nym','system')),
    status TEXT NOT NULL CHECK (status IN ('proposed','accepted','rejected','applied')),
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX idx_identity_changes_status ON identity_changes(status, id);

CREATE TABLE goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('user','nym','system')),
    priority INTEGER NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
    status TEXT NOT NULL CHECK (status IN ('proposed','active','paused','done','cancelled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_goals_status ON goals(status, priority);
