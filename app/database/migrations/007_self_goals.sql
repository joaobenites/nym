-- Fase 5.1: objetivos que o Nym cria por conta própria, com o motivo e as evidências registrados.

ALTER TABLE goals ADD COLUMN source TEXT NOT NULL DEFAULT 'user'
    CHECK (source IN ('user', 'exploration', 'desire'));
ALTER TABLE goals ADD COLUMN motivation TEXT;              -- por que o Nym quis isso (texto do modelo, guardado como registro)
ALTER TABLE goals ADD COLUMN refs TEXT NOT NULL DEFAULT '{}';  -- {"memories": [...], "dream_items": [...], "cycles": [...]}
UPDATE goals SET source = 'exploration' WHERE id IN (SELECT goal_id FROM exploration_tasks WHERE goal_id IS NOT NULL);

-- Diário de vontades: cada vez que o Nym pensa no que quer fazer, fica registrado o que viu, o que decidiu e por quê.
CREATE TABLE desire_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'done', 'nothing', 'failed')),
    trigger TEXT NOT NULL,
    seen TEXT NOT NULL DEFAULT '{}',          -- ids do que foi mostrado ao modelo
    created_goal_ids TEXT NOT NULL DEFAULT '[]',
    approved_exploration_ids TEXT NOT NULL DEFAULT '[]',
    dropped TEXT NOT NULL DEFAULT '[]',       -- ideias descartadas pelo código, com o motivo
    error TEXT
);
CREATE INDEX idx_desire_runs_started ON desire_runs(started_at);

-- Novo motivo para puxar assunto: "decidi fazer tal coisa".
CREATE TABLE pending_messages_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN
        ('dream_note', 'welcome_back', 'task_done', 'task_failed', 'task_permission', 'goal_created')),
    topic TEXT NOT NULL,
    refs TEXT NOT NULL DEFAULT '[]',
    cycle_id INTEGER REFERENCES dream_cycles(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'delivered', 'expired', 'dismissed')),
    created_at TEXT NOT NULL,
    delivered_at TEXT
);
INSERT INTO pending_messages_new (id, kind, topic, refs, cycle_id, status, created_at, delivered_at)
    SELECT id, kind, topic, refs, cycle_id, status, created_at, delivered_at FROM pending_messages;
DROP TABLE pending_messages;
ALTER TABLE pending_messages_new RENAME TO pending_messages;
CREATE INDEX idx_pending_messages_status ON pending_messages(status, id);
