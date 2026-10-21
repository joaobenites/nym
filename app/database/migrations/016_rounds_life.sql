-- Fase 9 (+ correção do uso real).
-- 1) Rodadas: só os passos da rodada atual decidem se ela travou (antes, uma falha antiga cancelava tudo que viesse depois).
ALTER TABLE goals ADD COLUMN round_from_step INTEGER NOT NULL DEFAULT 0;

-- 2) Projetos: coisas dele que duram dias, feitas de vários objetivos em sequência.
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    why TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'done', 'dropped')),
    refs TEXT NOT NULL DEFAULT '{}',
    closing_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
ALTER TABLE goals ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL;

-- 3) Diário: uma página por dia, escrita por ele a partir do que aconteceu de verdade (`facts`), com o que quer amanhã.
CREATE TABLE diary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL UNIQUE,            -- AAAA-MM-DD no fuso do computador
    text TEXT NOT NULL,
    tomorrow TEXT,                       -- intenção para o dia seguinte (vira material das vontades)
    facts TEXT NOT NULL,                 -- JSON com os registros do dia que ele usou
    memory_id INTEGER REFERENCES memories(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
