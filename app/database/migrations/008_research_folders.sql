-- Fase 6: pesquisa na internet com registro das fontes, e pastas do usuário que o Nym pode ler.

-- Toda busca e toda página lida ficam aqui: consulta, fonte, data e o que voltou.
CREATE TABLE research_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    kind TEXT NOT NULL CHECK (kind IN ('search', 'fetch')),
    query TEXT NOT NULL,                  -- termos da busca ou URL lida
    backend TEXT,
    status TEXT NOT NULL CHECK (status IN ('ok', 'error', 'cached')),
    results TEXT NOT NULL DEFAULT '[]',   -- [{title, url, snippet}] (busca) ou [{title, url}] (página)
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_research_log_query ON research_log(kind, query, created_at);
CREATE INDEX idx_research_log_goal ON research_log(goal_id, id);

-- Pastas que o USUÁRIO autorizou o Nym a ler (só leitura). Só a API do usuário mexe aqui.
CREATE TABLE authorized_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
