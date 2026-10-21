-- Fase 7.1: gostos e prioridades próprios do Nym.
-- Um gosto só existe com evidência real (pesquisas, criações, objetivos, sonhos, conversas, reações); o código
-- recalcula tudo a partir desses registros. O usuário pode vetar um gosto (nunca volta a ser formado).

CREATE TABLE tastes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,            -- radical normalizado do assunto
    label TEXT NOT NULL,                 -- como aparece ("digimon")
    valence INTEGER NOT NULL CHECK (valence IN (-1, 1)),   -- 1 curte, -1 não curte
    strength REAL NOT NULL,              -- 0..1
    score REAL NOT NULL,                 -- soma das evidências com decaimento
    items INTEGER NOT NULL,              -- quantas evidências distintas
    days INTEGER NOT NULL,               -- em quantos dias diferentes
    sources TEXT NOT NULL DEFAULT '{}',  -- {"pesquisa": 3, "criacao": 1, ...}
    origin TEXT NOT NULL CHECK (origin IN ('own', 'shared')),   -- own = veio do que ele fez/sonhou; shared = do que vocês conversam
    status TEXT NOT NULL CHECK (status IN ('active', 'faded', 'vetoed')),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    shared_at TEXT                       -- última vez que ele puxou assunto por causa deste gosto
);
CREATE INDEX idx_tastes_status ON tastes(status, strength);

CREATE TABLE taste_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    taste_id INTEGER NOT NULL REFERENCES tastes(id) ON DELETE CASCADE,
    source TEXT NOT NULL,                -- pesquisa, criacao, objetivo, sonho, conversa, reacao, frustracao
    ref INTEGER NOT NULL,                -- id do registro de origem
    weight REAL NOT NULL,
    day TEXT NOT NULL,
    snippet TEXT NOT NULL,
    UNIQUE (taste_id, source, ref)
);

-- Novo motivo para puxar assunto: algo de que ele gosta.
CREATE TABLE pending_messages_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN
        ('dream_note', 'welcome_back', 'task_done', 'task_failed', 'task_permission', 'goal_created', 'followup', 'growth',
         'taste_share')),
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
