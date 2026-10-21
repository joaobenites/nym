# Nym — contexto do projeto (leia antes de mexer)

Companheiro digital pessoal (vpet, no espírito de Digimon e MegaMan Battle Network), local-first, no Windows.
O objetivo do dono é um **amigo virtual**, não um chatbot. Idioma de tudo: português do Brasil.

## Nomes
O nome oficial é **Nym**. O subsistema de sonhos (Fase 4) se chama **Somnium**. Nunca use "Echo".

## Estado das fases
| Fase | Estado |
|---|---|
| 1 Núcleo (FastAPI, SQLite, Ollama, chat) | Concluída e validada pelo dono (pytest passou) |
| 2 Identidade persistente | Concluída e validada pelo dono |
| 3 Memória | Concluída; pytest validado pelo dono, funcionando com o Gemma real |
| 4 Somnium + presença + estado interno | **Implementada e testada com modelo simulado. Falta validar com o Gemma real** (JSON de sonho válido, qualidade das hipóteses, naturalidade da iniciativa) |
| 4.1 Tempo entre conversas + sonho como dele + voz menos chatbot | **Implementado e testado com modelo simulado (252 testes). Falta validar com o Gemma real** |
| 5 Planejamento e autonomia (objetivos, fila, permissões, retomada) + ferramentas de baixo risco | **Implementada e testada com modelo simulado (~290 testes; os de API só rodam no Windows dele). Falta validar com o Gemma real** (JSON do plano, avaliação, avisos) |
| 5.1 Objetivos próprios do Nym (vontades a partir de sonhos, perguntas, interesses, lembranças) | **Implementada e testada com modelo simulado (~300 testes). Falta validar com o Gemma real** |
| 6 Pesquisa na internet + pastas autorizadas + velocidade (streaming, keep_alive, prompt com prefixo fixo) + interface nova | **Implementada e testada com modelo e internet simulados (~330 testes). Falta validar no Windows com o Gemma 4 e internet real** |
| 6.1 Correções do uso real: compromissos do chat viram objetivo, compose_note, sonhos em rodízio, vontades variadas, janela em blocos | **Implementada e testada com modelo simulado (~375 testes). Falta validar no Windows** |
| 6.2 Amigo virtual: Tavily sem chave, lembretes "perguntar depois", evolução em estágios, avatar SVG por estado real, notificações | **Implementada e testada com modelo/internet simulados (~365 testes). Falta validar no Windows** |
| 7 Ateliê (criações), auto-melhoria proposta, aprendizado por experiência (lições com placar, receitas, reações) | **Implementada e testada com modelo simulado (380 testes). Falta validar no Windows com o Gemma 4** |
| 7.1 Gostos e prioridades próprios (gostos calculados de registros reais, veto do usuário, peso no chat/vontades/iniciativa) | **Implementada e testada com modelo simulado (416 testes na nuvem; os de API só rodam no Windows). Falta validar no Windows com o Gemma 4** |
| 8 Sentidos: visão (imagem/câmera/tela no chat, imagens de pastas) e assistir vídeos do YouTube | **Implementada e testada com modelo e YouTube simulados (~400 testes). Falta validar no Windows** |
| 8.1 Celular na rede local (pareamento por código, `app/core/access.py`) + interface (Nym no topo, dias, menu ＋, PWA) | **Implementada; guarda testada na nuvem, rotas só no Windows. Falta testar num celular real** |
| 8.2 Autonomia: rodadas (sem chutar argumentos), recomeço sozinho, tempo livre, "confiar no risco médio" | **Implementada e testada com modelo simulado (454 testes). Falta validar no Windows com o Gemma 4** |
| 9 Vida contínua: dia (`app/life/day.py`), diário com "Amanhã:", projetos, assuntos guardados, humor nas vontades + correções do uso real (rodadas, passos mal formados, objetivos presos) | **Implementada e testada com modelo simulado (465 testes) e simulada sobre o banco real. Falta validar no Windows com o Gemma 4** |
| 9.1 Vpet: cuidados no estilo tamagotchi/Digimon (`app/core/pet.py`, página Cuidados): fome, diversão, higiene, carinho, disciplina, sujeirinhas, sono, chamados e descuidos, modo jogo | **Implementada e testada na nuvem (core/integração; rotas só no Windows). Falta validar no Windows com o Gemma 4** |
| 9.2 Parceria estilo MegaMan/Lan: voz de parceiro (`policy.py`), vpet como camada leve, tela PET para celular (`link.html`) | **Implementada; falta validar no Windows/celular com o Gemma 4.** Faltam missões em dupla e sincronia (discutir com o dono) |
| 9.3 Honestidade proativa (achado do teste real de 18/10): trava de lembrança inventada no chat (`voice.ungrounded_claims`, issue `lembranca_sem_registro`, 1 nova versão) + notas de sonho sem molde repetido (`presence.is_stale_note`, regra no prompt do sonho) | **Implementada e testada na nuvem com modelo simulado (524 testes; API só no Windows). Falta validar no Windows com o Gemma 4** |
| 9.4 Cérebro trocável (`python -m app.brain_check`, filtro `<think>`) + tempo livre sem travar (reserva de ideias, repetidas no prompt, retentativa 15/30/60 min, padrão 30 min) + energia como reserva | **Implementada e testada com modelo simulado (testes na nuvem). Falta rodar o teste de cérebro no Windows (qwen3.5:9b × gemma4)** |
| 9.5 LoRA do Nym: `app/training/export.py` (dados reais filtrados: vício de voz/👎 fora, 👍 em dobro, diário) + `training/` (Unsloth, instalação no Ollama) | **Exportação testada na nuvem. Treino NUNCA executado: primeiro teste na RTX 3060 do dono (risco de faltar VRAM para o 12B)** |
| 9.6 Amigo honesto (achados do uso real de 18-19/10, medidos sobre uma cópia do banco): ação inventada no chat (`voice.unbacked_actions`, issue `acao_inventada`, correção em mensagem própria no streaming), pedido explícito vira objetivo pelo CÓDIGO (`app/core/requests.py`), quem fala = dono ou visita (`app/core/speaker.py`), "o que você fez hoje" só com registros, objetivos/notas sobre si mesmo limitados (`desires.is_self_referential`, `presence.is_meta_topic`), iniciativa sem repetir a abertura | **Implementada e testada na nuvem com modelo simulado (588 testes; API só no Windows) e reproduzida sobre cópia do banco real. Falta validar no Windows com o Gemma 4** |
| 10.1 Conversa de amigo (achados da conversa REAL de 21/10 como "Claudio", feita pelo navegador embutido em 127.0.0.1:8000): ritmo da fala (`app/core/rhythm.py`), estado real do pedido cobrado (`requests.asks_progress/status_block`), favorito inventado (`voice.ungrounded_tastes`, issue `gosto_inventado`), "não sou uma pessoa/o que tenho processado" sem ele perguntar, resposta longa a fala curtíssima (`desproporcional`), entrevista (`pergunta_de_novo`), gostos em comum (`tastes.common_ground`) | **Implementada e testada na nuvem (627 testes; API só no Windows).** O código novo SÓ vale depois de reiniciar o Nym; ainda não foi reconversado com ele já reiniciado |
| 10 Rosto reativo funcional | **Próxima.** O dono tem ideias e projetos de base: conversar com ele ANTES de começar |
| Voz (ouvir/falar), hardware | Depois |

Pendências da Fase 4 que só o ambiente real responde: `pytest` completo (incluindo `tests/test_api.py`, ~225 testes); um ciclo
de sonho com o Gemma (`life.html` → Sonhar agora); a iniciativa do Nym (não pode insistir nem soar como notificação); a página
`life.html` num navegador. Pendências anteriores (Fase 3): o `pytest` completo no Windows (incluindo `tests/test_api.py`, que
exige fastapi e httpx); a qualidade da extração com o Gemma 3 12B (resumos, JSON válido, fatos perdidos); a página
`frontend/memory.html` num navegador; a paginação "Mensagens anteriores" do chat.

## Como rodar (PowerShell, na pasta do projeto)
```powershell
.venv\Scripts\python.exe -m pytest          # esperado: ~152 passed, 0 skipped
.venv\Scripts\python.exe -m app             # http://127.0.0.1:8000
```
Não ative o venv com `Activate.ps1`: a política de execução do PowerShell bloqueia. Chame `.venv\Scripts\python.exe` direto.
Sem fastapi/httpx instalados, os testes de API são ignorados e o resto roda só com a biblioteca padrão:
`python -m unittest discover -s tests -t .`

## Arquitetura (resumo)
- `app/config.py`: configuração (`NYM_*`, `.env`). `app/logging_setup.py`: logs; **feche os handlers** em testes (Windows trava arquivo aberto).
- `app/database/`: SQLite, migrações `NNN_*.sql` transacionais (sem BEGIN/COMMIT dentro delas), repositórios. `secure_delete` ligado.
- `app/models/`: `LLMProvider` (base) e `OllamaProvider` (só `urllib`). `chat()` aceita `temperature` e `json_mode`.
- `app/core/`: `chat_service` (um chat único contínuo), `identity` + `prompt_builder` + `policy` (regras fixas no código), `voice` (tom), `timesense` (tempo), `goals`, `activity`.
- `app/tasks/` (planner, desires, manager + TaskWorker) e `app/tools/` (base, builtin, web, folders, registry com permissões).
- `frontend/`: `shell.js` (barra lateral comum) + páginas; `app.js` faz streaming por `/api/chat/stream` (NDJSON).
- `app/dreams/`: Somnium (`engine` tudo-ou-nada, `scheduler`, `journal`, `associations` = prompt+validação, `exploration`). `app/core/state.py` e `app/core/presence.py`.
- `app/memory/`: `store`, `retrieval`, `extraction`, `consolidation` (+ `MemoryWorker`), `service`, `text`.
- `app/api/`: rotas FastAPI. `frontend/`: HTML/JS puros (`index`, `identity`, `memory`).
- `tests/`: `unittest` (o pytest também roda). Dublês só em `tests/` (`fakes.py`, `memtools.py`).

## Decisões que não devem ser desfeitas sem conversar com o dono
- **Um único chat**, sem lista de conversas. Conversas antigas ficam no banco, fora do chat.
- Núcleo só com a biblioteca padrão; dependências de execução: fastapi e uvicorn.
- Identidade e memória vivem no banco, independentes do modelo. Regras de segurança/permissões ficam em `app/core/policy.py`, **nunca** em dados editáveis.
- Propostas de identidade do Nym ficam pendentes até o usuário aceitar; não há endpoint HTTP para criá-las (só código interno, para o Somnium).
- Memória: o modelo propõe, o **código decide** (evidências obrigatórias, "dito pelo usuário" só com mensagem do usuário, segredos descartados, gravação atômica).
- Exclusão de memória é de verdade (ver política no topo de `app/memory/store.py`).
- Sonhos: o modelo propõe, o código valida (ids reais, hipótese ≤ 0,6, segredos descartados); hipóteses viram memória *hipótese*; mudanças de identidade só como proposta pendente.
- Presença (mudou na Fase 5, a pedido do dono): o Nym chama sempre que houver motivo real (tarefa pronta/falhou/pede
  permissão, nota de sonho, "voltou"), SEM limite diário nem intervalo por padrão (0 = sem limite, configurável). Cada
  motivo é entregue uma vez; "voltou" não se repete sem resposta; horário de silêncio e piso técnico de 1 min ficam.
- Tarefas (Fase 5): só objetivos `mode='task'` executam; os antigos (`ongoing`) são só contexto. O modelo propõe passos,
  o código valida. Permissão conferida na hora de rodar; risco alto nunca automático; só a API do usuário muda permissões.
  Profundidade de decomposição 1. Ferramentas sem rede e sem acesso fora de `data/notes/`.
- Vontades (Fase 5.1, a pedido do dono): o Nym cria objetivos próprios (`origin='nym'`, `source='desire'|'exploration'`)
  com `motivation` e `refs` obrigatórios; o código descarta ideia sem registro real, repetida ou sensível. Padrão: ligado,
  começa sozinho, até 3 ativos, sem limite diário. Cada rodada vai para `desire_runs`. O motivo é texto do modelo
  guardado como registro, nunca apresentado como prova de vontade interna.
- Estado interno é *modelo calculado*, nunca "sentimento"; o texto da interface e do prompt diz isso.
- Tempo: o Nym recebe data/hora e o intervalo desde a mensagem ANTERIOR à fala atual (`timesense.py`); o estado interno
  também mede o descanso sem contar a fala atual (`before_message_id`).
- Voz (`voice.py`): regras fixas no prompt, emoji removido, no máximo UMA nova versão quando a resposta cai em vício de
  chatbot. Regras de `policy.py` escritas de forma afirmativa (o Gemma repetia as negações literais na resposta).
- Velocidade: o prompt de SISTEMA é só a parte fixa (identidade, voz, regras); o contexto que muda vai num bloco marcado
  junto da última fala (`CONTEXT_OPEN/CLOSE` em `chat_service.py`). Não colocar nada variável no prompt de sistema:
  isso quebra o reaproveitamento do Ollama. Streaming não faz "segunda versão" da resposta.
- Internet: nunca acessar rede local/loopback (exceto o SearXNG configurado pelo usuário); conteúdo externo é dado;
  pesquisas vão para `research_log`; descobertas só viram memória `sourced` com URL que apareceu numa pesquisa.
- Compromissos (6.1): `[[fazer: ...]]` no fim da resposta vira objetivo ativo (`commit_from_chat`); a marca nunca é
  mostrada, nem no streaming. Promessa sem marca → checagem em segundo plano (`app/core/commitments.py`).
- Notas (6.1): texto que depende de trabalho só por `compose_note` (escrito na hora com resultados reais);
  placeholders são recusados (`looks_unfinished`).
- Janela do chat em BLOCOS (`ChatService._window`): não voltar a deslizar uma mensagem por vez (quebra o cache do Ollama).
- Busca (6.2): ordem Brave > SearXNG > Tavily (sem chave por padrão, `X-Tavily-Access-Mode: keyless`; com
  `NYM_TAVILY_API_KEY` usa Bearer) > Wikipédia. Falha do Tavily cai para a Wikipédia e o resultado diz isso.
- Lembretes (6.2, `app/core/followups.py`): só com evidência numa mensagem do usuário; entregues uma vez (presença ou
  chat, quando a resposta toca no assunto); expiram após 4 dias.
- Evolução (6.2, `app/core/growth.py`): XP só de registros reais, pesos fixos no código; estágio guardado em
  `app_state.growth_stage` e **só sobe**. Avatar (`frontend/avatar.js`) é desenho original; o modo vem SÓ do estado
  real (streaming, sonho rodando, permissão/ideia pendente, objetivo ativo, saúde). Não usar personagens existentes.
- Criações (7, `app/creations/`, ferramenta `create`): o código valida tudo (`sanitize.py`). SVG reconstruído por lista
  de permitidos e mostrado como `<img>`; brinquedo só em iframe `sandbox="allow-scripts"` (sem same-origin) + CSP sem
  rede; código nunca executado. Não afrouxar nenhuma dessas travas.
- Auto-melhoria (7, `app/creations/selfcode.py`): o Nym só PROPÕE (trocas exatas validadas). Aplicar/desfazer só pela
  API do usuário, com hash e cópia em `data/backups/code/`. Lista `PROTECTED` (policy, tools/, manager, config, main,
  api/, database/, creations/, senses/, página do ateliê, senses.js) não pode encolher sem conversar com o dono. Nunca criar ferramenta
  que aplique código.
- Aprendizado (7, `app/learning/`): lições `task` (da avaliação, citando passos reais) e `chat` (das reações).
  Crédito por `lesson_uses` (uma vez por alvo); aposentadoria automática quando falha mais do que ajuda. Receita =
  plano de objetivo parecido concluído. Nada disso mexe nos pesos do modelo; não descrever como "treino".
- Gostos (7.1, `app/core/tastes.py`): o código calcula tudo de registros reais (pesquisa, criação, objetivo, sonho, conversa,
  reação, frustração); o modelo nunca decide um gosto. Teto de peso por fonte, histerese, meia-vida de 21 dias; "não curtir"
  exige reação do usuário; só a API do usuário veta (veto é permanente). O bloco de gostos vai no contexto da última fala,
  nunca no prompt de sistema. Iniciativa `taste_share` só para gosto forte, `own`, com o usuário em silêncio. É preferência
  calculada, não sentimento.
- Sentidos (8, `app/senses/`): câmera e tela SÓ por clique do usuário na interface (nunca ferramenta, nunca
  automático); imagem conferida pela assinatura; por padrão guarda só a descrição (`perceptions`). O que ele viu entra
  no histórico colado à fala do usuário (`vision.history_note`) e na consolidação marcado como "visto, não dito".
  Vídeo: só YouTube; legenda traduzida evitada (429); vídeo baixado em pasta temporária e apagado; yt-dlp e ffmpeg são
  OPCIONAIS (núcleo continua só com a biblioteca padrão). Falha de visão é registrada como "não consegui enxergar".
- Rede local (8.1): `NYM_LAN=true` → escuta em 0.0.0.0. Middleware em `main.py`: loopback entra direto, mas com Host
  local (anti DNS rebinding); outros IPs só com cookie de aparelho pareado (hash no banco). Gerar código/desconectar só
  do loopback. Nunca afrouxar sem conversar com o dono; nunca expor fora da rede local.
- Autonomia (8.2): planejamento em RODADAS (`planner.guessed_arg` descarta argumento chutado; plano/avaliação só com chutes
  = pedir de novo, não pausar). Objetivo `origin='nym'` que trava → `_schedule_retry` (retry_after, previous_attempt, sem
  aviso) até `self_retries`; objetivos do usuário avisam na hora. Tempo livre = gatilho `free_time` em `_desire_trigger`
  (sem objetivos dele ativos, chat parado ≥10 min, a cada `free_time_minutes`). `trust_medium` nunca toca risco alto.
- Vida contínua (9): o dia é SÓ registros (`day.facts`, sem modelo). Diário só de dias já terminados e não vazios, com os
  fatos guardados junto; vira memória episódica. Coisas que ele faz sozinho não geram aviso (`goal.done_kept_for_later`)
  — aparecem no "enquanto ele esteve fora" (chat após 3 h, `AWAY_SECONDS`) e no "voltou". Projetos: até 3 ativos;
  continuar projeto real conta como registro. Rodada = `goals.round_from_step` (falha antiga não cancela passo novo).
- Vpet (9.1, `app/core/pet.py`): modelo de bichinho de jogo CALCULADO do tempo real (nunca "sofrimento"); o código decide números,
  recusas, chamados e descuidos, o modelo só fala do que está registrado (bloco "Seu corpinho de vpet" no contexto da última
  fala, nunca no prompt de sistema; regra fixa em `policy.py`). Ele NUNCA morre (piso nas barras); desgaste limitado a 6 h por
  retomada, reduzido à noite/dormindo; "modo jogo" (pausa) congela tudo e não gera descuido. Chamado `pet_call` vira
  iniciativa uma vez; cuidar do que ele pediu encerra o chamado. Dias de cuidado dão XP em `growth`.
- Cérebro (9.4): o modelo é trocável por `NYM_MODEL`; escolha com `app/brain_check.py` (cópia do banco, nunca o
  original). O dono quer "um cérebro mais legal" e algo que dê para mexer por dentro: o caminho combinado é modelo aberto
  (candidato: Qwen3.5 9B) + LoRA do Nym treinado na GPU dele. Treinar do zero só como laboratório (nanoGPT), não como cérebro.
- LoRA (9.5): o treino roda FORA do app (`training/`, ambiente `.venv-train` separado; o núcleo continua só com a
  biblioteca padrão). Só o dono roda o treino; o Nym nunca treina a si mesmo nem tem ferramenta para isso. O modelo
  treinado entra pelo `NYM_MODEL` depois de ganhar no teste de cérebro; o Gemma original fica instalado para voltar.
- Pastas: só a API do usuário autoriza; só leitura, só texto, caminho resolvido sempre dentro da pasta.
- Lembrança proativa (9.3): se a resposta afirma que o dono "me falou/disse/avisou que...", "a gente conversou sobre..." ou "lembro de quando...", o conteúdo precisa aparecer no que o Nym tem (identidade, falas do USUÁRIO, bloco de contexto); falas do próprio Nym nunca servem de prova. Senão o chat pede UMA nova versão. Heurística por palavras (`MIN_GROUNDING`). Notas de sonho que abrem com muleta ("É fascinante como...") ou com as mesmas 3 palavras de uma das 8 últimas são descartadas (o sonho continua no diário).
- Honestidade de ação (9.6): o texto do modelo não é ação. Dizer no passado que "já olhou/pesquisou/assistiu/achou" só vale se o
  registro do contexto sustenta (`voice.unbacked_actions`; fora do registro: falas do usuário, objetivos abertos e o próprio
  pedido ainda sem resultado). Sem registro: `acao_inventada`. Sem streaming reescrever, o `ChatService._correct` manda UMA
  mensagem curta de correção logo após `done` (evento `correction`, auditoria `chat.correction`); se a correção também
  escorregar, nada é enviado. Só problemas de honestidade (`HONESTY_ISSUES`) disparam isso.
- Pedido explícito (9.6, `app/core/requests.py`): "procure/pesquise/busque/dá uma olhada..." vira objetivo na hora por
  `commit_from_chat` (deduplicado) e o contexto avisa que AINDA NÃO há resultado. Detecção conservadora (negação, hipótese,
  pergunta sobre o que ele fez e formas ambíguas como "você pesquisa" ficam de fora).
- Quem fala (9.6, `app/core/speaker.py`): dono = o nome que o usuário disse ser dele ("eu me chamo X"; guardado em
  `app_state.owner_name`, nunca trocado sozinho; banco antigo é preenchido pelas primeiras falas). "Aqui é o Y" cria uma visita
  que dura 45 min sem mensagens ou até o dono se apresentar. Mensagens que ele puxa sozinho são SEMPRE para o dono.
- Interesses no mundo (9.6): sobre uma cópia do banco real, o filtro marca 25 dos 48 objetivos próprios e 22 das 26 notas de sonho como
  conversa sobre o João, o sistema ou a própria autonomia. Agora o código limita: no máximo um objetivo autorreferente a cada 10
  (`desires.SELF_QUOTA_WINDOW`) e uma nota de sonho "sobre a relação" entre as últimas 8 (`presence.META_MAX_RECENT`).
  Iniciativas com a mesma abertura das últimas ganham UMA nova tentativa (`presence.repeats_opening`).
- Conversa de amigo (10.1): (1) `rhythm.block` lê a fala do dono (tamanho, risada, cansaço, despedida, empolgação) e escreve uma instrução curta
  no bloco de contexto da última fala (nunca no prompt de sistema); é só tom, heurística, o texto diz "pode estar errada". (2) Se o dono
  cobra um pedido ("e aí, já descobriu?"), o CÓDIGO injeta o estado real do último objetivo vindo do chat ("Situação do pedido dele");
  sem resultado, a resposta precisa admitir que ainda não saiu (`voice._admits_pending`), senão `acao_inventada` (honestidade: correção em
  mensagem própria). Achado: com o objetivo ainda em `needs_plan`, o Nym disse "Consegui encontrar... Tamagotchi, 1996". (3) Nome próprio
  apresentado como favorito/gosto que não está nos registros = `gosto_inventado` (honestidade). (4) Os vícios de TOM (`desproporcional`,
  `pergunta_de_novo`) só pedem nova versão no chat sem streaming; no streaming a guia é o bloco de ritmo. (5) Gostos em comum = gosto
  curtido com falas do dono (>=3) E registros próprios dele (>=3): linha extra no bloco de gostos, sem inventar.
- Tom do Nym: amigo, mensagens curtas, sem tom de atendente. Mas **nunca** inventar lembranças nem afirmar consciência (regras fixas em `policy.py`).

## Regras de trabalho (da especificação do dono)
Etapas pequenas e testáveis; examinar os arquivos antes de alterar; não reescrever o que funciona; sem funções vazias ou
retornos fictícios; validar entradas e tratar erros; testes para o que é crítico; não remover dados persistentes em
atualizações; sem credenciais no código; sem acesso irrestrito ao sistema operacional; não confundir texto gerado pelo modelo
com ação executada. Ao concluir uma etapa, informar: o que foi feito, arquivos, decisões, como rodar, como testar,
problemas conhecidos, próximo passo. Declarar com clareza o que está implementado, parcial ou pendente.

## O dono
Windows, GPU RTX 3060 de 12 GB, Ollama com `gemma4:12b-it-qat` (trocou do gemma3:12b na Fase 6). Prefere explicações diretas.
Sugestões devem focar nos interesses dele e no projeto.

## Próximo passo recomendado (atualizado na Fase 9)
1. pytest no Windows e deixar rodar 2-3 dias: Vida → Diário/Projetos; conferir se as páginas usam só o que aconteceu,
   se a intenção "Amanhã:" vira objetivo no dia seguinte e se os objetivos dele terminam (rodadas + recomeço).
2. Fase 10: rosto reativo funcional — o dono tem ideias e projetos de base; perguntar antes de desenhar a arquitetura.

## Próximo passo recomendado (histórico, Fase 8)
1. pytest no Windows e "Verificação manual da Fase 8": a visão do Gemma 4 (qualidade e tempo da descrição), câmera e
   tela no navegador, um vídeo real com yt-dlp + ffmpeg.
2. Próximo sentido: ouvir (faster-whisper local para áudio sem legenda e para a voz dele) e falar (Piper).

## Próximo passo recomendado (histórico, Fase 7.1)
1. pytest no Windows (inclui `tests/test_api_tastes.py`) e a "Verificação manual" do README em "Gostos e prioridades":
   ver se os gostos que aparecem em `life.html` fazem sentido (palavras genéricas que escapem: acrescentar a `GENERIC` em
   `tastes.py`) e se o Gemma 4 usa o bloco "Seus gostos" com naturalidade, sem recitar a lista.
2. Sugestões: gosto por pessoas/lugares (nomes próprios), gostos em comum explícitos com o dono, resumo do dia no sonho, voz.

## Próximo passo recomendado (histórico, Fase 7)
1. pytest no Windows e a "Verificação manual da Fase 7" do README: SVG e HTML que o Gemma 4 gera de verdade (se
   errar muito o formato, ajustar `KIND_PROMPTS` em `app/tools/create.py`), lições que ele tira, propostas de código.
2. Depois: voz local (Piper/faster-whisper) ou ESP32 com o avatar.

## Próximo passo recomendado (histórico, Fase 6.2)
1. pytest no Windows e a "Verificação manual da Fase 6.2" do README (Tavily real, avatar, lembretes, notificações).
2. Sugestões em fila (escolha do dono): voz local (Piper para falar, faster-whisper para ouvir); resumo do dia feito
   no sonho ("hoje eu..."); acesso pelo celular na rede local (`NYM_HOST=0.0.0.0` + senha); ESP32 com telinha
   mostrando o avatar e o estado (`/api/growth` + `/api/state`); minijogos/rituais diários que alimentam a convivência.

## Próximo passo recomendado (histórico, Fase 6)
1. pytest no Windows (inclui `tests/test_api_tasks.py` com streaming, pastas e pesquisas) e a "Verificação manual da Fase 6".
2. Medir o tempo real das respostas (aparece embaixo de cada uma) e, se ainda estiver lento, olhar `load_s` e
   `prompt_eval_s` na auditoria: carregamento lento = keep_alive; prompt lento = contexto grande (`NYM_NUM_CTX`, `NYM_CONTEXT_MESSAGES`).
3. Fase 7 (restante): painéis de memória e sonhos no mesmo padrão visual; depois Fase 8 (voz e avatar ligado ao `InnerState`).

## Próximo passo recomendado (histórico, Fase 5)
1. Rodar o pytest completo no Windows (inclui `tests/test_api_tasks.py`) e a "Verificação manual da Fase 5" do README.
2. Ver se o Gemma devolve plano e avaliação em JSON válido; se errar, ajustar `PLAN_PROMPT`/`EVAL_PROMPT` em `app/tasks/planner.py`.
3. Fase 6: pesquisa na internet (com fontes e datas) e pastas autorizadas do usuário, como novas ferramentas no registro.
   Talvez: criar objetivo pelo chat.

## Próximo passo recomendado (histórico, Fase 4)
1. Rodar o pytest completo e corrigir o que falhar no ambiente real.
2. Validar a Fase 4 com o Gemma (ver "Verificação manual da Fase 4" no README) e ajustar `app/dreams/associations.py` (prompt)
   se o modelo errar o formato ou inventar.
3. Depois: Fase 5 (objetivos e execução de tarefas, usando as `exploration_tasks` aprovadas) e Fase 8 (avatar que reflete
   `InnerState` e o estado real do sistema).
