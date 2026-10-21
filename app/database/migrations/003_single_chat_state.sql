-- Estado simples da aplicação (chave/valor).
CREATE TABLE app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Nym tem um único chat contínuo. Se o banco já tem conversas (Fases 1 e 2), a mais recente
-- vira o chat principal. As demais permanecem no banco, intactas, mas fora do chat.
INSERT INTO app_state (key, value, updated_at)
SELECT 'main_conversation_id', id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM conversations ORDER BY updated_at DESC, id DESC LIMIT 1;
