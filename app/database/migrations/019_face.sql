-- Fase 10: rosto reativo. Cada reação mostrada fica registrada: qual expressão, quem escolheu (o próprio Nym pela marca
-- [[rosto: ...]], o código lendo a fala do dono, ou o sistema) e a resposta a que ela pertence.
CREATE TABLE face_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expression TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('model', 'dono', 'sistema')),
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_face_events_created ON face_events(created_at);
