-- Fase 8: sentidos. Tudo o que o Nym viu (imagem, câmera, tela, imagem de pasta) ou assistiu (vídeo) fica aqui,
-- com a descrição que o modelo de visão fez e de onde veio. A imagem em si só é guardada se o usuário quiser
-- (NYM_KEEP_IMAGES); por padrão fica só a descrição.
CREATE TABLE perceptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('image', 'camera', 'screen', 'file', 'video')),
    source TEXT NOT NULL,                 -- 'câmera', 'tela', nome do arquivo, URL do vídeo
    title TEXT,
    description TEXT NOT NULL,            -- o que foi visto / o resumo do que assistiu
    details TEXT NOT NULL DEFAULT '{}',   -- JSON: canal, duração, legenda, quadros descritos, modelo...
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
    image_path TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_perceptions_message ON perceptions(message_id);
CREATE INDEX idx_perceptions_created ON perceptions(created_at);
