-- Fase 4 (Somnium) + presença: diário de sonhos, tarefas de exploração e mensagens por iniciativa do Nym.

-- Mensagens que o Nym enviou por iniciativa própria (não em resposta a algo).
ALTER TABLE messages ADD COLUMN initiative INTEGER NOT NULL DEFAULT 0;

CREATE TABLE dream_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL CHECK (trigger IN ('manual', 'idle', 'scheduled')),
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'partial', 'interrupted', 'failed')),
    reason TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    selected_ids TEXT NOT NULL DEFAULT '[]',
    stage_log TEXT NOT NULL DEFAULT '[]',
    review TEXT,
    model TEXT,
    model_calls INTEGER NOT NULL DEFAULT 0,
    prompt_chars INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    error TEXT
);
CREATE INDEX idx_dream_cycles_started ON dream_cycles(started_at);

CREATE TABLE dream_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL REFERENCES dream_cycles(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN
        ('association', 'hypothesis', 'open_question', 'dream_note', 'consolidation', 'archive', 'identity_proposal')),
    content TEXT NOT NULL,
    refs TEXT NOT NULL DEFAULT '[]',
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    status TEXT CHECK (status IS NULL OR status IN ('open', 'closed')),
    created_at TEXT NOT NULL
);
CREATE INDEX idx_dream_items_cycle ON dream_items(cycle_id, kind);

-- Perguntas que mereceriam investigação. A execução (pesquisa, ferramentas) é das Fases 5 e 6.
CREATE TABLE exploration_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER REFERENCES dream_cycles(id) ON DELETE SET NULL,
    question TEXT NOT NULL,
    rationale TEXT,
    refs TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'dismissed', 'done', 'expired')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_exploration_status ON exploration_tasks(status);

-- Assuntos que o Nym poderia puxar. O texto final é escrito no momento de falar, pelo próprio Nym.
CREATE TABLE pending_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('dream_note', 'welcome_back')),
    topic TEXT NOT NULL,
    refs TEXT NOT NULL DEFAULT '[]',
    cycle_id INTEGER REFERENCES dream_cycles(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'delivered', 'expired', 'dismissed')),
    created_at TEXT NOT NULL,
    delivered_at TEXT
);
CREATE INDEX idx_pending_messages_status ON pending_messages(status, id);
