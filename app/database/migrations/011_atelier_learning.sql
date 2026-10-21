-- Fase 7: o Nym cria coisas (ateliê), propõe melhorias no próprio código e aprende com a experiência.

-- Criações: textos, desenhos (SVG higienizado), brinquedos (HTML/JS que só roda numa caixa isolada do navegador)
-- e códigos (Python, só para ler; nunca executado pelo Nym). Mesmo título = nova versão (parent_id).
CREATE TABLE creations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('text', 'drawing', 'toy', 'code')),
    title TEXT NOT NULL,
    about TEXT,                          -- do que se trata / o que ele quis fazer
    content TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    parent_id INTEGER REFERENCES creations(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'archived')),
    goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_creations_status ON creations(status, id);

-- Propostas de melhoria do próprio código: o Nym escreve, o usuário decide. Aplicar guarda cópia para desfazer.
CREATE TABLE code_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    why TEXT NOT NULL,
    file TEXT NOT NULL,                  -- caminho relativo à pasta do projeto
    edits TEXT NOT NULL,                 -- JSON [{find, replace}]
    diff TEXT NOT NULL,
    base_hash TEXT NOT NULL,             -- sha256 do arquivo quando a proposta foi feita
    new_hash TEXT NOT NULL,              -- sha256 depois de aplicar
    warnings TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (status IN ('proposed', 'applied', 'rejected', 'reverted', 'stale')),
    goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
    backup_path TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX idx_code_proposals_status ON code_proposals(status, id);

-- Lições: o que deu certo ou errado ao fazer as coisas. Entram no planejamento (tarefas) ou na conversa e são
-- reforçadas/enfraquecidas pelo resultado de verdade. Aposentadas quando falham mais do que ajudam.
CREATE TABLE lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL CHECK (scope IN ('task', 'chat')),
    text TEXT NOT NULL,
    tools TEXT NOT NULL DEFAULT '[]',
    evidence TEXT NOT NULL DEFAULT '{}', -- {"goal": id, "tasks": [ids]} ou {"feedback": [ids]}
    uses INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    reinforced INTEGER NOT NULL DEFAULT 0, -- a mesma lição tirada de novo, de outra experiência
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    retired_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_lessons_scope ON lessons(scope, status);

-- Onde cada lição foi usada (num plano ou numa resposta), para dar o crédito certo quando o resultado chega.
CREATE TABLE lesson_uses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    target TEXT NOT NULL CHECK (target IN ('goal', 'message')),
    target_id INTEGER NOT NULL,
    outcome INTEGER,                     -- NULL = ainda sem resultado; 1 deu certo; -1 não deu
    created_at TEXT NOT NULL
);
CREATE INDEX idx_lesson_uses_target ON lesson_uses(target, target_id);

-- Reações do usuário: em respostas do Nym e em criações.
CREATE TABLE feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL CHECK (target IN ('message', 'creation')),
    target_id INTEGER NOT NULL,
    value INTEGER NOT NULL CHECK (value IN (-1, 1)),
    comment TEXT,
    learned INTEGER NOT NULL DEFAULT 0,  -- já usada para tirar lição de conversa
    created_at TEXT NOT NULL,
    UNIQUE (target, target_id)
);
