-- Fase 8.2: autonomia. Objetivo do Nym que travou tenta de novo sozinho (com o que deu errado à vista) antes de
-- incomodar o usuário. retry_round = quantas vezes ele já recomeçou; retry_after = quando recomeça.
ALTER TABLE goals ADD COLUMN retry_round INTEGER NOT NULL DEFAULT 0;
ALTER TABLE goals ADD COLUMN retry_after TEXT;
ALTER TABLE goals ADD COLUMN previous_attempt TEXT;
