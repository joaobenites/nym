-- Fase 6.1: objetivos combinados na conversa ("vou pesquisar isso pra você") viram tarefa na hora.
-- Guarda de qual mensagem do chat o compromisso saiu, para mostrar e auditar.
ALTER TABLE goals ADD COLUMN chat_message_id INTEGER;
