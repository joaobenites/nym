-- Fase 5: objetivos executáveis, tarefas, histórico de ações e permissões de ferramentas.

-- Objetivos ganham modo de execução. Os que já existem continuam 'ongoing' (só contexto, como antes):
-- nada que você cadastrou nas Fases 2 a 4 passa a ser executado sozinho.
ALTER TABLE goals ADD COLUMN mode TEXT NOT NULL DEFAULT 'ongoing' CHECK (mode IN ('ongoing', 'task'));
ALTER TABLE goals ADD COLUMN completion_criteria TEXT;
ALTER TABLE goals ADD COLUMN deadline TEXT;
ALTER TABLE goals ADD COLUMN result TEXT;
ALTER TABLE goals ADD COLUMN plan_state TEXT NOT NULL DEFAULT 'idle'
    CHECK (plan_state IN ('idle', 'needs_plan', 'running', 'evaluating', 'finished'));
ALTER TABLE goals ADD COLUMN plan_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE goals ADD COLUMN replans INTEGER NOT NULL DEFAULT 0;
ALTER TABLE goals ADD COLUMN model_calls INTEGER NOT NULL DEFAULT 0;
ALTER TABLE goals ADD COLUMN last_error TEXT;

-- Cada passo executável. Executados em ordem dentro do objetivo: um passo só roda quando os anteriores terminaram.
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER REFERENCES goals(id) ON DELETE CASCADE,
    step INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT NOT NULL DEFAULT '{}',
    risk TEXT NOT NULL CHECK (risk IN ('low', 'medium', 'high')),
    origin TEXT NOT NULL CHECK (origin IN ('user', 'nym', 'system')),
    status TEXT NOT NULL CHECK (status IN
        ('awaiting_permission', 'queued', 'running', 'done', 'failed', 'blocked', 'cancelled')),
    approved INTEGER NOT NULL DEFAULT 0,          -- o usuário autorizou ESTA tarefa
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX idx_tasks_status ON tasks(status, id);
CREATE INDEX idx_tasks_goal ON tasks(goal_id, step, id);

-- Histórico de ações: tudo o que aconteceu com cada tarefa e objetivo.
CREATE TABLE task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER REFERENCES goals(id) ON DELETE CASCADE,
    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    event TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_task_events_goal ON task_events(goal_id, id);
CREATE INDEX idx_task_events_task ON task_events(task_id, id);

-- Permissões por ferramenta, definidas pelo usuário. Sem linha aqui, vale o padrão do código (pelo risco).
CREATE TABLE tool_permissions (
    tool TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK (mode IN ('auto', 'ask', 'deny')),
    updated_at TEXT NOT NULL
);

-- Explorações aprovadas viram objetivos; o resultado volta para a exploração.
ALTER TABLE exploration_tasks ADD COLUMN goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL;
ALTER TABLE exploration_tasks ADD COLUMN result TEXT;

-- Novos motivos para o Nym puxar assunto: tarefa concluída, tarefa que falhou, pedido de permissão.
-- O SQLite não altera CHECK de tabela existente: a tabela é recriada, com os dados copiados.
CREATE TABLE pending_messages_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN
        ('dream_note', 'welcome_back', 'task_done', 'task_failed', 'task_permission')),
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

-- Presença sem limites (pedido do dono). Antes, max_per_day = 0 significava "desligado"; agora 0 é "sem limite".
-- Quem tinha 0 salvo continua com a presença desligada, como escolheu.
UPDATE app_state SET value = json_set(value, '$.enabled', json('false'))
    WHERE key = 'presence_settings' AND json_extract(value, '$.max_per_day') = 0;
UPDATE app_state SET value = json_set(value, '$.max_per_day', 0, '$.min_gap_minutes', 0)
    WHERE key = 'presence_settings';
