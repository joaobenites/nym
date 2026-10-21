-- Fase 10 (vpet): cuidados no estilo tamagotchi/Digimon. Tudo é MODELO CALCULADO (como um bichinho de jogo), não sofrimento.
CREATE TABLE pet_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    fullness REAL NOT NULL,          -- 0..100 (100 = barriguinha cheia)
    fun REAL NOT NULL,
    hygiene REAL NOT NULL,
    discipline REAL NOT NULL,
    affection REAL NOT NULL,
    messes INTEGER NOT NULL DEFAULT 0,
    mess_due_at TEXT,                -- quando a próxima sujeirinha aparece (depois de comer)
    sleeping INTEGER NOT NULL DEFAULT 0,
    sleep_until TEXT,
    paused INTEGER NOT NULL DEFAULT 0,   -- "modo jogo": congela necessidades e chamados
    last_fed_at TEXT,
    last_played_at TEXT,
    last_cleaned_at TEXT,
    last_trained_at TEXT,
    updated_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE pet_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,              -- feed, snack, play, clean, train, pet, sleep, wake, pause, resume, mess, care_mistake, refused
    detail TEXT NOT NULL DEFAULT '{}',
    day TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_pet_events_created ON pet_events(created_at);

-- Chamados: ele "pede" cuidado quando uma necessidade passa do limite. Sem resposta a tempo = descuido (registrado).
CREATE TABLE pet_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    need TEXT NOT NULL CHECK (need IN ('fome', 'tedio', 'sujeira')),
    status TEXT NOT NULL CHECK (status IN ('open', 'answered', 'missed')),
    started_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE pending_messages_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN
        ('dream_note', 'welcome_back', 'task_done', 'task_failed', 'task_permission', 'goal_created', 'followup', 'growth',
         'taste_share', 'pet_call')),
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
