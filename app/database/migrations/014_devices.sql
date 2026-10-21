-- Acesso pelo celular na rede local: aparelhos pareados com um código mostrado no computador.
-- Guarda só o hash do token (nunca o token em si). Desconectar = revoked_at preenchido.
CREATE TABLE devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_seen TEXT,
    last_ip TEXT,
    revoked_at TEXT
);
