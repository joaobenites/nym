-- Memória (Fase 3): episódica, semântica e de identidade numa tabela, separadas por `kind`.
-- Estado epistêmico (confirmed/sourced/hypothesis/unverified) e origem ficam em colunas
-- próprias: fato nunca se confunde com hipótese.
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('episodic', 'semantic', 'identity')),
    content TEXT NOT NULL,
    outcome TEXT,
    epistemic TEXT CHECK (epistemic IS NULL OR epistemic IN ('confirmed', 'sourced', 'hypothesis', 'unverified')),
    origin TEXT NOT NULL CHECK (origin IN ('user_stated', 'nym_inferred', 'external_source', 'user_edit', 'system')),
    importance REAL NOT NULL DEFAULT 0.5 CHECK (importance >= 0 AND importance <= 1),
    confidence REAL NOT NULL DEFAULT 0.5 CHECK (confidence >= 0 AND confidence <= 1),
    recurrence INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK (status IN ('active', 'archived', 'superseded')),
    superseded_by INTEGER REFERENCES memories(id),
    entities TEXT NOT NULL DEFAULT '[]',
    occurred_at TEXT NOT NULL,
    ended_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_accessed_at TEXT,
    access_count INTEGER NOT NULL DEFAULT 0,
    CHECK (kind = 'episodic' OR epistemic IS NOT NULL)
);
CREATE INDEX idx_memories_kind_status ON memories(kind, status);

-- Proveniência: de onde veio cada memória. Sem FK em message_id: o usuário pode apagar
-- mensagens, e a exclusão limpa as referências explicitamente.
CREATE TABLE memory_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL CHECK (source_type IN ('message', 'manual', 'url')),
    message_id INTEGER,
    ref TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_memory_sources_memory ON memory_sources(memory_id);
CREATE INDEX idx_memory_sources_message ON memory_sources(message_id);

CREATE TABLE memory_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    to_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    relation TEXT NOT NULL CHECK (relation IN
        ('part_of', 'follows', 'supports', 'contradicts', 'related', 'duplicate_of', 'supersedes')),
    created_at TEXT NOT NULL,
    UNIQUE (from_id, to_id, relation)
);
CREATE INDEX idx_memory_links_to ON memory_links(to_id);

-- Histórico: cada linha só contém conteúdo da PRÓPRIA memória (nunca de outra), para que
-- apagar uma memória apague tudo o que ela contém.
CREATE TABLE memory_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    actor TEXT NOT NULL CHECK (actor IN ('user', 'system', 'nym')),
    reason TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_memory_history_memory ON memory_history(memory_id);

-- Trabalhos de consolidação (estado dos processos em segundo plano).
CREATE TABLE memory_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_message_id INTEGER NOT NULL UNIQUE,
    end_message_id INTEGER NOT NULL,
    message_count INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'done', 'failed', 'skipped')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    memory_id INTEGER REFERENCES memories(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_memory_jobs_status ON memory_jobs(status, id);
