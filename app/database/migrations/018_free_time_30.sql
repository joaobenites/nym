-- Uso real (18/10): com tempo livre a cada 90 min ele passava a maior parte do dia parado ("não faz nada sozinho").
-- O padrão passou a 30 min. Só muda quem ainda estava no padrão antigo (90); um valor escolhido pelo dono fica.
UPDATE app_state
SET value = json_set(value, '$.free_time_minutes', 30)
WHERE key = 'autonomy_settings'
  AND json_valid(value)
  AND json_extract(value, '$.free_time_minutes') = 90;
