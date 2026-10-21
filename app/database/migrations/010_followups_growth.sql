-- Fase 6.2 (sugestões): coisas para perguntar depois e aviso de evolução.

-- "Tenho prova sexta" -> no dia, ele pergunta como foi. Sai da conversa (consolidação), com evidência real.
CREATE TABLE followups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,                 -- como ele perguntaria ("Como foi a prova?")
    about TEXT,                         -- o assunto, curto
    due_at TEXT NOT NULL,               -- a partir de quando faz sentido perguntar
    status TEXT NOT NULL CHECK (status IN ('pending', 'delivered', 'dismissed', 'expired')),
    evidence TEXT NOT NULL DEFAULT '[]',
    episode_id INTEGER REFERENCES memories(id) ON DELETE CASCADE,   -- apagar o episódio apaga o lembrete
    created_at TEXT NOT NULL,
    delivered_at TEXT
);
CREATE INDEX idx_followups_due ON followups(status, due_at);

CREATE TABLE pending_messages_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN
        ('dream_note', 'welcome_back', 'task_done', 'task_failed', 'task_permission', 'goal_created', 'followup', 'growth')),
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
