# Nym

Companheiro digital pessoal (a ideia de um vpet, no espírito de Digimon e MegaMan Battle Network),
local-first, com identidade e memória persistentes. O objetivo é um **amigo virtual**, não um chatbot.
Este repositório está na **Fase 9 — vida contínua: dia, diário, projetos e assuntos guardados** (8.2: autonomia; 8.1: celular; 8: sentidos).

## Estado atual

| Item | Estado |
|---|---|
| Config (`.env`, variáveis `NYM_*`) | Implementado e testado |
| SQLite + migrações versionadas e transacionais | Implementado e testado |
| Persistência de conversas e mensagens | Implementado e testado |
| Abstração do LLM + provider Ollama | Implementado e testado (contra servidor HTTP simulado) |
| Tratamento de modelo indisponível (mensagem do usuário preservada) | Implementado e testado |
| Troca de modelo sem perder histórico | Implementado e testado |
| API FastAPI (`/health`, `/api/chat`, conversas, mensagens) | Escrito; testes em `tests/test_api.py` ainda **não executados** |
| Interface web: chat único em tela cheia, com paginação do histórico | Funciona na sua máquina (chat); a paginação "Mensagens anteriores" ainda não foi vista em navegador |
| Teste real contra um Ollama de verdade | **Pendente** (precisa rodar na sua máquina) |
| Botão "Interromper" | **Parcial**: cancela a espera no navegador; o servidor ainda pode concluir a inferência |
| Streaming de respostas | Pendente (previsto para depois) |
| **Fase 2:** registro de identidade no banco, com semente padrão | Implementado e testado |
| Histórico de mudanças (valor anterior, novo, justificativa, origem, confiança, estado) | Implementado e testado |
| Propostas do Nym pendentes até aprovação; política opcional de aplicação automática | Implementado e testado (só pelo código; ver abaixo) |
| Identidade persistida no contexto do modelo, com orçamento de caracteres | Implementado e testado |
| Regras operacionais fixas (no código, fora do alcance de qualquer edição) | Implementado e testado |
| Objetivos persistentes (registro e estado) | Implementado e testado; decomposição e execução ficam na Fase 5 |
| Endpoints da identidade e dos objetivos | Escritos; testes em `tests/test_api.py` rodam na sua máquina |
| Página `identidade` (editar, arquivar, aceitar/rejeitar, histórico, objetivos) | Escrita; **não testada em navegador** |
| **Fase 3:** memória episódica, semântica e de identidade, com estado epistêmico (confirmado, hipótese, não verificado, de fonte) | Implementado e testado |
| Consolidação do chat em episódios e fatos, com evidências obrigatórias, em segundo plano e só com o chat parado | Implementado e testado com um modelo **simulado**; **não testado com o Gemma real** |
| Proveniência (cada memória aponta para mensagens reais), histórico de edições, contradições registradas | Implementado e testado |
| Deduplicação e recorrência (no momento da gravação e em lote) | Implementado e testado |
| Recuperação contextual com 7 componentes configuráveis, e exclusão do que já está no contexto | Implementado e testado (precisão@1 de 5/5 num conjunto pequeno) |
| Consultar, corrigir, arquivar e apagar (de verdade) pela API e pela página `memory.html` | API testada; **página não testada em navegador** |
| Memória de trabalho (6.1 da especificação) | **Não implementada**: hoje o "contexto de trabalho" é só a janela do chat |
| Embeddings / busca vetorial | **Não implementado** (de propósito; só se a recuperação lexical se mostrar insuficiente) |
| **Fase 4:** ciclo de sonho em 7 etapas (seleção, revisão, associação, consolidação, exploração, reflexão sobre identidade, registro) | Implementado e testado com modelo **simulado**; **não testado com o Gemma real** |
| Diário de sonhos persistente; o chat consulta o registro real quando perguntam de sonhos | Implementado e testado |
| Gatilhos: manual, ocioso, agendado; pausa, cancelamento, limites de tempo/chamadas/itens, retomada após queda | Implementado e testado |
| Tudo-ou-nada: falha ou cancelamento antes de gravar não altera nenhuma memória | Implementado e testado |
| Hipóteses de sonho viram memórias *hipótese* com ligação às evidências; nunca fato | Implementado e testado |
| Propostas de identidade a partir de padrões repetidos em episódios diferentes (aprovação sua) | Implementado e testado |
| Tarefas de exploração: registradas, com limites; aprovar/dispensar | Implementado e testado. **Executá-las: não** (Fases 5 e 6) |
| Estado interno (energia, curiosidade, vínculo) calculado de dados reais, no prompt e no cabeçalho | Implementado e testado |
| Presença: o Nym puxa assunto (depois de um sonho ou quando você volta), com limites | Implementado e testado com modelo simulado |
| Página `life.html` (estado, presença, sonhos, perguntas, exploração) | Escrita; **não testada em navegador** |
| Limites de CPU/memória do processo nos sonhos | **Não implementado** (só tempo, chamadas ao modelo e itens) |
| Atualizar "resumos de projetos" (7.2, etapa 4) | **Não implementado** |
| Quem gera propostas de identidade | O Somnium (só como tipo *preferência*). Não há endpoint HTTP para criá-las, de propósito |
| **Fase 5:** objetivos executáveis (descrição, origem, prioridade, critério, prazo, estado, resultado) | Implementado e testado |
| Decomposição em passos pelo modelo, validada pelo código (ferramenta existe, argumentos válidos, limite de passos) | Implementado e testado com modelo **simulado**; **não testado com o Gemma real** |
| Fila com ordem por objetivo, tentativas, tempo limite por ferramenta, avaliação do resultado e replanejamento limitado | Implementado e testado |
| Retomada após reinício (tarefa "rodando" volta para a fila) | Implementado e testado |
| Permissões por ferramenta (automático / perguntar / negado), conferidas na hora de rodar; risco alto nunca automático | Implementado e testado |
| Histórico de ações (`task_events`) e auditoria | Implementado e testado |
| Ferramentas: data e hora, cálculo seguro, busca na memória, notas em `data/notes/` | Implementado e testado. Internet e pastas suas: Fase 6 |
| Explorações aprovadas dos sonhos viram objetivos; o resultado volta para a exploração | Implementado e testado |
| Avisos do Nym: tarefa pronta, falhou, pede permissão (presença sem limite, por escolha sua) | Implementado e testado com modelo simulado |
| O chat sabe o que ele fez de verdade (bloco "Suas tarefas") | Implementado e testado |
| Rotas `/api/tasks/*` e página `tasks.html` | Rotas com testes em `tests/test_api_tasks.py` (rodam na sua máquina); **página não testada em navegador** |
| **Fase 5.1:** o Nym cria objetivos próprios (depois de um sonho, ou de tempos em tempos se há algo novo), com motivo e registros que o sustentam | Implementado e testado com modelo **simulado**; **não testado com o Gemma real** |
| Ideias sem registro real, repetidas ou com dado sensível são descartadas pelo código (e anotadas no diário de vontades) | Implementado e testado |
| Ele decide investigar sozinho as explorações dos sonhos que couberem | Implementado e testado |
| Configuração da autonomia (ligar, começar sem seu ok, limites) na página Tarefas | API testada na sua máquina; **página não testada em navegador** |
| **Fase 6:** pesquisa na internet (Wikipédia sem chave; Brave ou SearXNG opcionais), leitura de páginas | Implementado e testado com internet **simulada**; **não testado com a internet real** |
| Proteções: nada da rede local, só texto, limites de tamanho e tempo, conteúdo externo marcado como dado | Implementado e testado |
| Diário de pesquisas (consulta, fonte, data, resultados) e reaproveitamento de buscas iguais em 24 h | Implementado e testado |
| `save_finding`: guarda uma descoberta como lembrança "de fonte externa", só com URL realmente pesquisada | Implementado e testado |
| Pastas autorizadas por você (só leitura, só texto, sem sair da pasta) | Implementado e testado (link simbólico: só testado no Linux) |
| **Velocidade:** resposta em streaming, "Parar" interrompe de verdade, modelo mantido carregado, modo de pensar desligado, prompt com parte fixa reaproveitada | Implementado e testado com modelo simulado; **ganho real a medir na sua máquina** |
| **Interface nova:** barra lateral, chat com painel "Agora" (estado, o que ele faz, permissões, pedir tarefa, último sonho) | Vista em navegador com dados falsos; **não testada com o Nym rodando** |
| Pedir tarefa pelo chat | Pelo painel "Pedir para ele fazer" ao lado do chat (não por frase na conversa) |
| Criar objetivo pelo chat ("anota isso pra mim") | **Não implementado**: hoje o objetivo é criado na página Tarefas |
| Sub-objetivos (decomposição com mais de um nível) | **Não implementado** (de propósito: profundidade 1 é o limite) |
| **Fase 6.2:** busca na web inteira sem chave (Tavily keyless; chave grátis opcional), com volta para a Wikipédia | Implementado e testado com internet **simulada**; **não testado com a internet real** |
| Lembretes "perguntar depois" (extração com evidência, entrega pela presença ou no chat, expiração) | Implementado e testado com modelo simulado |
| Evolução em estágios a partir de dados reais (só sobe; comenta quando sobe) | Implementado e testado |
| Avatar SVG original com forma por estágio e animação pelo estado real | Visto em navegador com dados falsos; **não testado com o Nym rodando** |
| Notificações do navegador quando ele chama com a aba escondida | Escrito; **não testado em navegador real** |
| **Fase 7:** criações (texto, desenho SVG higienizado, brinquedo HTML isolado, código só para ler), com versões | Implementado e testado com modelo **simulado**; isolamento do brinquedo conferido em navegador (sem rede) |
| Propostas de melhoria do próprio código: leitura, trocas validadas, arquivos protegidos, aplicar/desfazer só pelo usuário | Implementado e testado (inclui fim de linha do Windows) |
| Lições de tarefa (da avaliação, com evidência), placar pelo resultado, aposentadoria automática, receitas de planos | Implementado e testado com modelo simulado |
| Reações 👍/👎 no chat e nas criações; lições de conversa com placar | Implementado e testado; botões vistos em navegador com dados falsos |
| Página Ateliê (criações, melhorias de código, aprendizados) | Vista em navegador com dados falsos; **não testada com o Nym rodando** |
| Executar o código que ele escreve | **Não implementado, de propósito** (só leitura; brinquedos rodam isolados no navegador) |
| **Fase 8:** visão (imagem, câmera, tela) no chat, com registro do que viu e histórico | Implementado e testado com modelo simulado; câmera vista em navegador com câmera falsa; **não testado com o Gemma 4 real** |
| Assistir vídeos do YouTube (legenda original, quadros com ffmpeg, miniaturas como alternativa, resumo em partes) | Implementado e testado com YouTube **simulado** (quadros extraídos com ffmpeg de verdade); **não testado com o YouTube real** |
| Olhar imagens de pastas autorizadas | Implementado e testado |
| Ouvir áudio sem legenda / voz | **Pendente** (reconhecedor de fala local, junto com a voz) |
| **Fase 8.1:** acesso pelo celular na rede local com pareamento por código, proteção contra DNS rebinding | Guarda testada aqui; rotas testadas só no Windows (exigem fastapi); **não testado num celular real** |
| Interface: Nym no topo, painel com fechar, dias, digitando, menu ＋ no celular, ícone/manifesto | Vista em navegador (desktop e celular simulado) com dados falsos |

### Um chat só

Nym tem **uma única conversa contínua**: sem lista de conversas e sem "nova conversa". Tudo fica no mesmo fio,
que sobrevive a reinícios. Bancos antigos (Fases 1 e 2) com várias conversas são migrados: a mais recente vira o
chat único e as outras **ficam no banco, intactas, mas fora do chat** (a Fase 3 pode aproveitá-las como memória).

Limitação importante enquanto a memória (Fase 3) não existe: o modelo só recebe as últimas
`NYM_CONTEXT_MESSAGES` mensagens (padrão 20, e até `NYM_CONTEXT_CHARS` caracteres). Tudo continua salvo e visível na
tela, mas o que ficou para trás nesse limite o Nym ainda não "lembra".

### Memória (Fase 3)

**Como uma conversa vira lembrança.** O chat é cortado em *episódios* (pausa de mais de `NYM_EPISODE_GAP_MINUTES`,
padrão 20, ou 30 mensagens). Quando você fica sem falar por `NYM_MEMORY_IDLE_SECONDS`, o Nym pede ao modelo (com
temperatura 0.2 e saída em JSON) um resumo do episódio e os fatos que **você** disse. O código então valida tudo:

- cada item precisa citar ids de mensagens reais do trecho; o que não tiver evidência válida é descartado;
- "dito pelo usuário" só vale se a evidência inclui mensagem **sua**; senão vira *hipótese* (confiança limitada a 0,7);
- senhas, documentos e números longos são descartados (heurística, não garantia);
- tudo é gravado numa única transação: ou o episódio inteiro entra, ou nada entra.

Se o Ollama estiver fora do ar, o trecho fica **pendente** e é retomado depois (não conta como falha). Resposta inválida
do modelo conta tentativa; esgotadas (3), o trecho vira "falhou" e aparece na página de memória.

**Certeza.** `confirmado` (você disse), `hipótese` (o Nym deduziu), `não verificado`, `de fonte externa` (Fase 6).
O modelo vê esse rótulo ao lado de cada lembrança e é instruído a tratar hipóteses como hipóteses.

**Contradições.** Nada é apagado em silêncio. Se você disser o contrário de algo registrado e houver assunto em comum, a
lembrança antiga fica *substituída* (preservada, com ponteiro para a nova). Caso contrário (hipótese contra fato, ou assuntos
sem relação), as duas permanecem, ligadas por "contradiz", e o conflito aparece na página de memória e no prompt.

**Recuperação.** Nota = lexical 0,50 + importância 0,15 + recência 0,10 + confiança 0,10 + recorrência 0,05 + objetivos 0,05 +
relação 0,05 (pesos em `RetrievalWeights`). Só entra o que tem relevância lexical mínima ou ligação com um resultado forte.
Poucos "fatos centrais" confirmados e importantes (como o seu nome) sempre entram. Em `memory.html` há um testador que
mostra a nota de cada componente.

**Observações de identidade.** Padrões como "prefere respostas curtas" só viram *candidatos* depois de aparecerem em pelo
menos 3 mensagens de 2 episódios diferentes. Nesta fase eles **não** alteram a identidade; quem os transformará em
propostas é o Somnium (Fase 4).

**Política de exclusão.**
- *Apagar* remove a lembrança, suas fontes, ligações e histórico (o banco usa `secure_delete`, e o WAL é truncado em seguida).
  Fica só uma lápide sem conteúdo no log de auditoria. As mensagens do chat e o resumo do episódio **permanecem**.
- *Apagar com as mensagens de origem* remove também essas mensagens e **toda** memória que cite alguma delas (inclusive o
  resumo do episódio, que pode conter a informação).
- Um trecho já consolidado nunca é reprocessado, então o que você apagou não reaparece sozinho. Se você voltar a falar do
  assunto, ele pode ser registrado de novo.

**Limitações conhecidas (leia antes de confiar).**
- **A qualidade depende do modelo.** Os testes usam um modelo simulado. Com o Gemma real, pode haver fatos perdidos,
  resumos fracos ou JSON inválido; o código descarta o que não valida, mas não consegue inventar o que o modelo não viu.
- A consolidação e o chat dividem a mesma GPU. Se você mandar mensagem durante uma consolidação, a resposta espera o Ollama
  terminar o trecho (alguns segundos).
- "Consolidar agora" fecha também o trecho em andamento; mensagens enviadas depois formam outro episódio.
- A deduplicação é por palavras (sem embeddings): paráfrases muito diferentes não são fundidas; o oposto, números e negações
  diferentes, nunca é fundido.
- Um processo só: não há proteção entre dois Nyms apontando para o mesmo banco.

### Vida: sonhos, estado interno e presença (Fase 4)

Nada disto afirma consciência. São mecanismos reais que dão continuidade e iniciativa ao Nym.

**Sonhos (Somnium).** Quando o chat fica parado por `NYM_DREAM_IDLE_MINUTES` (30) e as conversas já viraram memória, o
Nym revisa memórias reais: as novas desde o último sonho (até 70% do limite) e as antigas mais importantes. Uma chamada
ao modelo propõe revisão, ligações entre memórias, hipóteses, perguntas em aberto, tarefas de exploração e um assunto para
conversar. O código valida: tudo precisa citar ids de memórias que de fato foram revisadas, hipóteses têm confiança máxima
de 0,6 e entram como memórias *hipótese* (ligadas às evidências), e segredos são descartados. As etapas 3 a 5 são gravadas
numa transação só: se algo falhar, ou se você cancelar antes disso, **nenhuma memória é alterada**. Também na consolidação:
duplicatas são fundidas e episódios antigos, pouco importantes e nunca lembrados são *arquivados* (reversível).

- Limites: `NYM_DREAM_MAX_PER_DAY` (3), `NYM_DREAM_MIN_INTERVAL_HOURS` (6), `NYM_DREAM_MAX_SECONDS` (300),
  `NYM_DREAM_MAX_MODEL_CALLS`, `NYM_DREAM_MAX_MEMORIES`. Cancelar vale quando o modelo responder (uma chamada em andamento
  não é interrompida). Depois de uma falha, espera 30 min. Se o programa for encerrado no meio, o ciclo vira "interrompido".
- Agendado: `NYM_DREAM_SCHEDULE_HOUR` (hora local, -1 desliga). Pausar/retomar na página **Vida**.
- Observações de identidade que se repetem em 3 mensagens de 2 episódios diferentes viram **propostas pendentes** de
  *preferência* (você aprova em `identity.html`). Nunca mudam a identidade sozinhas.
- Se perguntarem do sonho, o Nym recebe o diário real; sem diário, é instruído a dizer que não há registro.
- Apagar uma memória apaga também o que os sonhos registraram sobre ela (itens, tarefas, assuntos e o resumo do ciclo).

**Estado interno.** Energia é uma reserva: cada mensagem SUA gasta um pouco e ela se recupera sozinha com o tempo
(pausas curtas já ajudam; um sonho numa pausa de verdade conta como cochilo); respostas e iniciativas dele não cansam.
Conversa em ritmo normal fica em "tranquilo"; só horas de conversa intensa levam a "cansado". (Antes, qualquer conversa
com mais de ~14 mensagens em 2 h, contando as dele, o deixava cansado: no uso real, 148 de 150 mensagens.)
Curiosidade sobe com pendências (saturando devagar) e novidades ainda não sonhadas, e cai logo depois de ele explorar;
vínculo cresce com regularidade e volume de conversa e esmaece
devagar com a ausência. É um modelo calculado (`app/core/state.py`), mostrado no cabeçalho do chat e orientando o tom do
prompt; o Nym é instruído a dizer, se perguntarem, que é um estado calculado e não um sentimento.

**Presença.** O Nym só puxa assunto (a) com um assunto deixado por um sonho ou (b) quando você abre o chat depois de
`NYM_PRESENCE_WELCOME_BACK_HOURS` (4) horas sem falar. **Nunca** quando: está desligado, em horário de silêncio
(23:00 a 08:00), no limite diário (2), a menos de 90 min da última iniciativa, logo depois de você escrever, com o chat
ocupado, ou se a última fala do chat já foi dele e você não respondeu. Se você falar enquanto ele escreve, a mensagem é
descartada. O texto é escrito na hora pelo próprio Nym, a partir de um motivo real. Ajustes na página **Vida**.
**Limitação:** a iniciativa só acontece com o chat aberto (a interface é quem pergunta); ele não manda notificação.

### Tom de companheiro

- A identidade padrão (v2) descreve um amigo virtual: mensagens curtas, sem listas nem tom de atendente, com humor e opinião.
- Bancos que já tinham a identidade padrão antiga são atualizados na primeira execução. O que você personalizou **não** é
  sobrescrito, e cada troca fica no histórico.
- `NYM_TEMPERATURE` (padrão 0.8) deixa o modelo mais solto. `NYM_NUM_PREDICT` limita o tamanho das respostas (0 = sem limite).
- `NYM_NUM_CTX` (padrão 8192) define a janela de contexto pedida ao Ollama. Se ela for menor do que o prompt de identidade
  mais o histórico, o Ollama corta o começo, e é lá que fica a identidade.
- **O modelo pesa muito.** Modelos pequenos (cerca de 3B) são fortemente treinados para soar como assistente e ignoram
  parte das instruções de estilo. Se o tom continuar de atendente, o próximo passo é testar um modelo maior ou mais voltado
  a conversa (`ollama list` e `NYM_MODEL`).

### Tempo e voz (passo 4.1)

- **Noção de tempo.** A cada resposta o Nym recebe a data e a hora no fuso do computador e quanto tempo passou desde a
  mensagem *anterior* à sua ("vocês se falaram pela última vez ontem à noite, há umas 20 horas"). Falas que vieram depois
  de uma pausa longa (3 h ou mais) ganham no histórico uma marca como `[2 dias depois]`. Tudo vem dos horários gravados
  no banco (`app/core/timesense.py`).
- **Sonho como coisa dele.** Se houve um sonho desde a última conversa, um resumo curto entra no contexto mesmo sem você
  perguntar, e ele pode comentar se fizer sentido. Perguntado, recebe o diário completo. O conteúdo continua vindo só do
  registro real; a regra fixa agora fala do sonho como dele, em vez de "processo técnico".
- **Voz.** Regras fixas de tom entram em todo prompt (`app/core/voice.py`): sem emoji, curto, sem abertura teatral, sem
  falar de si como IA a não ser que você pergunte, sem perguntar o que você acha dele. Emoji é removido da resposta
  (`NYM_ALLOW_EMOJI`). Se a resposta ainda cair num desses vícios, o Nym escreve **uma** nova versão e fica a melhor
  (`NYM_VOICE_RETRY`; custa uma chamada extra só nesses casos). O resultado fica na auditoria (`chat.reply` → `voice`).
- O verificador é por palavras: pode deixar passar algum vício ou, raramente, pedir nova versão sem precisar.

### Tarefas (Fase 5)

- Na página **Tarefas** você cria um objetivo ("anotar as ideias de vídeo que eu te contei") com critério e prazo
  opcionais. O Nym planeja os passos (o modelo propõe; o código descarta ferramenta inexistente e argumento inválido),
  executa em segundo plano, confere o resultado e, se faltar algo, faz no máximo `NYM_GOAL_MAX_REPLANS` rodadas extras.
- Ferramentas desta fase: `datetime_now`, `calculate`, `memory_search`, `list_notes`, `read_note` (automáticas) e
  `write_note` (pede sua permissão por padrão). Notas ficam em `data/notes/`, nunca fora de lá.
- Ele avisa no chat quando termina, quando não consegue e quando precisa de permissão. Por sua escolha, a presença não
  tem mais limite diário nem intervalo (`0` = sem limite). Cada motivo é dito uma vez; o horário de silêncio continua
  valendo e há um piso técnico de 1 minuto entre iniciativas.
- Objetivos antigos (das Fases 2 a 4) continuam só como contexto: nada que já existia passa a ser executado sozinho.
- Limites: `NYM_GOAL_MAX_STEPS`, `NYM_GOAL_MAX_REPLANS`, `NYM_GOAL_MAX_MODEL_CALLS`, `NYM_GOAL_MAX_PLAN_ATTEMPTS`,
  `NYM_TASK_MAX_ATTEMPTS`, `NYM_TASK_TIMEOUT_SECONDS`. **Pausar tudo** na página para a fila na hora.

### Vontades do Nym (Fase 5.1)

- Depois de cada sonho, ou a cada `interval_hours` se houver lembrança nova, pergunta em aberto ou exploração pendente,
  o Nym tem um "momento livre": olha para os sonhos recentes, as perguntas em aberto, os interesses e as lembranças e
  decide se quer fazer algo. Não é obrigado a querer nada.
- Cada objetivo dele guarda o **motivo** e as **referências** (lembranças, itens de sonho, sonhos). O código descarta
  ideia sem registro real, repetida, com dado sensível ou além dos limites; o descarte fica no diário de vontades.
- Explorações pendentes dos sonhos: ele mesmo decide investigar as que couberem.
- O "querer" é texto do modelo a partir desses registros, guardado como motivo. Não é prova de vontade interna.
  O que ele faz continua preso às ferramentas e permissões.
- Padrão (pedido seu): ligado, começa sem pedir seu ok, até 3 objetivos dele ao mesmo tempo, sem limite por dia.
  Tudo ajustável em **Tarefas → Vontades do Nym**. Com "começa sem pedir seu ok" desligado, as ideias ficam propostas
  até você aprovar.

### Velocidade (Fase 6)

- O texto aparece enquanto é gerado. **Parar** fecha a geração no Ollama e guarda o que já saiu.
- `NYM_KEEP_ALIVE=30m` mantém o modelo na GPU (o Ollama descarrega após 5 min por padrão; recarregar demora).
  O Nym também carrega o modelo ao abrir (`NYM_WARM_UP`).
- `NYM_THINK=false` desliga o "modo de pensar" do Gemma 4 (raciocínio oculto antes de responder, bem mais lento).
- O prompt de sistema agora só tem a parte fixa (identidade, voz, regras). O que muda a cada mensagem (hora, estado,
  lembranças, sonhos, tarefas) vai num bloco junto da sua última fala. Assim o Ollama reaproveita o processamento do
  começo da conversa e só calcula o fim.
- Embaixo de cada resposta aparecem o tempo total e os tokens por segundo; tudo também fica na auditoria (`chat.reply`).
- Trabalho de fundo que usa o modelo (planejar tarefas, vontades) espera o chat ficar parado alguns segundos.
- No streaming não há "segunda versão" da resposta (ela trocaria o texto na sua frente): os vícios de chatbot ficam
  registrados na auditoria. A rota antiga `/api/chat` mantém a segunda versão.

### Correções da Fase 6.1 (com base no uso real)

- **"Vou fazer" vira trabalho:** quando ele combina algo na conversa, termina a resposta com uma linha
  `[[fazer: ...]]` que você não vê; ela vira objetivo ativo na hora (aparece em "Fazendo" e no chat como
  "Começou a fazer"). Se ele prometer sem a marca ("vou pesquisar isso"), uma checagem curta em segundo plano
  registra o compromisso. Desligável com `NYM_CHAT_COMMITMENTS=false`.
- **Notas de verdade:** a ferramenta `compose_note` escreve a nota na hora, com os resultados reais dos passos
  anteriores (e atualiza a nota se o título já existe). Notas com "[inserir...]" ou "a ser preenchido" são recusadas.
  Notas inacabadas aparecem marcadas para ele nas vontades, para ele mesmo completar.
- **Sonhos sem repetição:** memórias antigas entram em rodízio (no máximo 12 por sonho), as novas vêm marcadas, e o
  sonho vê os anteriores com a regra de não repeti-los.
- **Vontades variadas:** ideia com muitas palavras em comum com um objetivo existente conta como repetida; o prompt
  pede coisas concretas e no máximo uma auto-reflexão por vez. O mesmo vale para as explorações dos sonhos.
- **Resposta mais rápida:** a janela do histórico anda em blocos, então o Ollama reaproveita o começo da conversa
  (antes reprocessava ~3000 tokens a cada mensagem).

### LoRA do Nym (9.5)
Treino de um LoRA em cima do Gemma 4 12B, na GPU do dono, com as conversas e o diário reais do Nym. O passo a passo
está em `training/LEIAME.md`: exportar (`python -m app.training.export`, testado), montar um ambiente de treino
separado (Unsloth), treinar (`training/train_lora.py`, **ainda não executado numa GPU real**), instalar no Ollama
(`training/install_ollama.py`, que copia o formato de conversa do modelo base) e comparar com o teste de cérebro antes
de trocar o `NYM_MODEL`. O que entra no treino: respostas do chat com contexto (sem as que tiveram vício de voz ou 👎,
e com as de 👍 em dobro) e as páginas do diário com o prompt e os fatos reais. Os dados ficam em `data/training/`.

### Cérebro: trocar o modelo e comparar (9.4)
O Nym não depende do modelo: identidade, memórias, diário e gostos ficam no banco. Trocar o "cérebro" é trocar
`NYM_MODEL` no `.env` (e reiniciar). Para escolher com dados, o **teste de cérebro** roda as tarefas reais do Nym
numa CÓPIA do banco (o original não é tocado) e mostra tempo e se o resultado passa na validação do próprio código:
3 mensagens de conversa, vontades (JSON), um plano (JSON) e uma página de diário.

```powershell
ollama pull qwen3.5:9b
.venv\Scripts\python.exe -m app.brain_check qwen3.5:9b gemma4:12b-it-qat
```
O relatório também vai para `data/logs/cerebro-*.txt`. Se gostar do novo, ponha `NYM_MODEL=qwen3.5:9b` no `.env`.
Modelos com "modo de pensar" (Qwen) rodam com `NYM_THINK=false`; se mesmo assim soltarem `<think>...</think>` no
texto, o provedor do Ollama remove (não é fala do Nym e quebraria o JSON). Próximo passo possível: um LoRA do Nym
(treinado na sua GPU com as conversas e o diário dele) em cima do modelo escolhido.

**Tempo livre mais ativo (uso real, 18/10).** Ele ficava parado: a intenção do diário já era um objetivo, cada rodada
propunha a mesma ideia, o código descartava ("repetido") e ele esperava 90 min. Agora: o modelo manda até 2 ideias de
reserva (se a primeira repetir, usa a próxima); as ideias repetidas das últimas rodadas aparecem no prompt ("não
proponha de novo"); a intenção do diário que já virou objetivo vem marcada ("pense no próximo passo"); rodada que não
deu em nada tenta de novo em 15 min (depois 30, 60...); e o tempo livre padrão caiu de 90 para 30 min (migração 018:
só muda quem estava no padrão antigo).

### Vida contínua (Fase 9)

O dia dele vira diário, o diário vira vontade, a vontade vira coisa feita, e a coisa feita vira conversa com você.
- **O dia dele** (`app/life/day.py`): montado só dos registros (o que terminou, decidiu, travou, criou, assistiu,
  pesquisou, sonhou, aprendeu e conversou). Vida → "Hoje até agora".
- **Diário** (`app/life/diary.py`): no dia seguinte (com o chat parado), ele escreve uma página curta em primeira
  pessoa sobre o dia que passou, só com esses registros, terminando com **"Amanhã: ..."**. A intenção entra nas
  vontades do dia seguinte, e a página vira uma lembrança ("Meu dia ..."), que os sonhos também revisitam. Dia sem
  nada não ganha página. Vida → Diário.
- **Projetos** (`app/life/projects.py`): coisas que duram dias, feitas de vários objetivos em sequência. Nas vontades
  ele vê o que já fez em cada projeto e escolhe o próximo passo; abre (até 3) e fecha sozinho. Você pode largar um em
  Vida → Projetos.
- **Assuntos guardados:** o que ele faz por conta própria não vira aviso na hora ("decidi fazer X", "terminei Y").
  Fica para quando você voltar: depois de 3 h sem conversa, ele recebe "enquanto ele esteve fora, você fez isto" e
  conta uma ou duas coisas, do jeito dele. Pedidos seus e coisas combinadas no chat continuam avisando quando terminam.
- **Humor nas escolhas:** o estado calculado (energia, curiosidade) entra nas vontades: cansado, prefere algo leve.

Correções do uso real (banco de 18/10) que travavam a autonomia:
- Uma falha ANTIGA de um objetivo cancelava na hora todos os passos novos dele (por isso "Retomar" e o recomeço
  sozinho não funcionavam: tudo virava "um passo anterior não deu certo"). Agora só a rodada atual conta.
- A avaliação não mostrava ao modelo o formato dos passos ("passo mal formado"); agora mostra, e o código entende
  variações ("ferramenta", "arguments", parâmetros soltos).
- Objetivo ativado pela página Identidade ficava "ativo" sem nunca planejar; agora replaneja.
- Vontade repetida de um objetivo dele que travou agora **retoma** esse objetivo em vez de ser descartada.

### Autonomia (Fase 8.2)

Olhando o banco real: de 37 objetivos dele, 20 travavam e viravam "não consegui, me ajuda". O motivo principal era o
planejamento: ele montava todos os passos antes de ver qualquer resultado e chutava o que não sabia
(`https://exemplo.com/...`, "Título da nota encontrada", "URL do resultado"). Mudou:
- **Trabalho em rodadas:** ele planeja só os passos que já sabe; depois de cada rodada vê os resultados reais e
  escolhe os próximos (até 6 rodadas, 16 chamadas ao modelo por objetivo). Passo com argumento chutado é descartado
  pelo código e ele precisa decidir de novo com o que tem.
- **Falhas leves não derrubam o trabalho:** ler uma nota que não existe devolve a lista das notas reais; `save_finding`
  com URL errada mostra as URLs que apareceram.
- **Recomeça sozinho:** objetivo DELE que trava guarda o que deu errado e recomeça meia hora depois, por outro caminho
  (até 2 vezes, configurável). Só depois disso ele te conta que não conseguiu. Objetivos seus continuam avisando na hora.
- **Tempo livre:** sem nada novo e sem nada dele em andamento, a cada 90 min (com o chat parado há 10 min) ele decide
  algo por conta: criar, assistir a um vídeo de um gosto dele, completar uma criação/nota, pesquisar algo que curte.
- **Confiar no risco médio:** em Tarefas → Ferramentas, um botão libera tudo de risco médio (internet, notas, vídeos)
  sem pedir permissão. Risco alto continua travado.
- Ajustes em Tarefas → Vontades: "Tempo livre a cada (min)" e "Quando trava, recomeça sozinho até (vezes)".

### No celular, pela rede local (Fase 8.1)

1. No `.env`, coloque `NYM_LAN=true` e abra o Nym de novo. O terminal mostra o endereço (ex.: `http://192.168.0.12:8000`).
   Se o Windows perguntar sobre o firewall, permita em **Redes privadas**.
2. No computador, clique em **📱 Celular** (barra lateral) → **Gerar código**.
3. No celular, no mesmo Wi-Fi, abra o endereço e digite o código. Pronto: o aparelho fica conectado (pode
   "Adicionar à tela inicial" para abrir como app).
- Segurança: no computador nada muda; de outro aparelho só entra quem foi pareado (código de 6 dígitos, uso único,
  10 minutos, 5 tentativas; endereço com erros demais fica bloqueado 10 min). O banco guarda só o hash do token.
  Gerar código e desconectar aparelhos só no próprio computador. Também barra o ataque de "DNS rebinding"
  (site malicioso falando com o Nym pelo seu navegador).
- Limites: pela rede local o navegador não libera câmera/tela ao vivo nem notificações (exige HTTPS). No celular,
  o botão **＋ → 🖼** abre a câmera do próprio celular para tirar a foto. Fora de casa não funciona (de propósito).

### Interface (Fase 8.1)

- O Nym aparece no topo da conversa (avatar vivo + o que está fazendo agora: "pensando…", "sonhando"...); no
  celular, tocar nele abre o painel "Agora" (fecha no ✕, tocando fora ou com Esc).
- Separadores de dia ("Ontem", "Hoje") e horário de cada mensagem ao parar o mouse em cima.
- Indicador de "digitando" enquanto ele pensa; no celular os botões de mostrar ficam atrás do **＋** e as reações
  aparecem ao tocar na mensagem.
- Barra inferior com nomes, campos de 16 px (sem zoom no iPhone), alvos de toque de 44 px, altura certa com a barra
  do navegador do celular, ícone e manifesto para a tela inicial.

### Sentidos (Fase 8)

- **Visão:** o Nym enxerga pelo modelo de visão do Ollama (o Gemma 4 enxerga; se o seu modelo de chat não enxergar,
  defina `NYM_VISION_MODEL`). Cada imagem é descrita e o que ele viu fica registrado (Vida → Sentidos), ligado à sua
  mensagem: ele continua lembrando do que viu nas mensagens seguintes e a consolidação de memória também recebe isso
  (marcado como "visto", não "dito por você").
- **Mostrar algo no chat:** 🖼 (ou cole/arraste uma imagem), 📷 câmera e 🖥 tela, ao lado da caixa de mensagem.
  A câmera abre só quando você clica, tira UMA foto quando você manda e desliga; a tela é capturada uma vez e o
  compartilhamento para na hora. As imagens são reduzidas no navegador (até 1024 px). Por padrão a imagem não é
  guardada, só a descrição (`NYM_KEEP_IMAGES=true` guarda em `data/perceptions/`). Não existe ferramenta para ele
  ligar a câmera sozinho.
- **Assistir vídeos do YouTube** (`watch_video`, pede permissão por padrão): título e canal pelo YouTube (sem chave);
  **ouve** pela legenda (do autor ou automática no idioma original) e **vê** quadros espalhados pelo vídeo, descritos
  pela visão; depois junta tudo num resumo ("Sobre o vídeo / O que vi e ouvi / O que achei / O que faltou"). Fica
  registrado como percepção e como lembrança "assisti a...", e a URL pode ser usada como fonte em `save_finding`.
  Peça no chat ("assiste esse vídeo: https://youtu.be/..."), ou ele assiste por vontade própria.
  - Para ouvir: `.venv\Scripts\python.exe -m pip install -r requirements-senses.txt` (yt-dlp).
  - Para ver quadros: ffmpeg (`winget install Gyan.FFmpeg`). O vídeo baixado (versão mais leve) é apagado na hora.
  - Sem eles: ele vê só as miniaturas que o YouTube publica e diz que não ouviu.
  - Vídeos longos: resume trechos espalhados; acima de `NYM_VIDEO_MAX_MINUTES` só ouve o começo.
- **Fotos das suas pastas** (`look_at_image`): imagens de pastas que você autorizou.
- **Limites honestos:** ele não ouve áudio sem legenda (falta um reconhecedor de fala local — fica para a fase da
  voz) e não assiste em tempo real; o resumo diz o que faltou. Câmera e tela só funcionam abrindo o Nym em
  `127.0.0.1`/`localhost` (o navegador exige endereço seguro).

### Verificação manual da Fase 8

1. Vida → Sentidos: confira se "Visão" está ● com o seu modelo.
2. No chat, clique em 📷, mostre algo e mande. A resposta deve falar do que apareceu; abra "ele viu pela câmera".
3. Clique em 🖥, escolha uma janela (um jogo, por exemplo) e pergunte o que ele acha.
4. Instale o yt-dlp e o ffmpeg, reinicie e mande "assiste esse vídeo: <link curto do YouTube>". Permita
   `watch_video`. Depois pergunte sobre o vídeo; em Vida → Sentidos aparece o resumo com o que ele ouviu e viu.
5. Em Vida → Sentidos, "Esquecer" num registro: ele deixa de lembrar daquilo no chat.

### Ateliê, auto-melhoria e aprendizado (Fase 7)

- **Criações:** a ferramenta `create` faz textos (poema, conto, reflexão), **desenhos** (SVG), **brinquedos**
  interativos (página HTML/JS: joguinho, animação, gerador de arte) e **códigos** (Python para você ler). O texto é
  escrito na hora, com o material real do objetivo; mesmo tipo e título = nova versão (a anterior fica no histórico).
  Ele cria quando você pede ("faz um desenho de...") ou por vontade própria. Página **Ateliê** → Criações.
- **Segurança das criações:** desenhos são reconstruídos só com elementos permitidos (sem script, links externos ou
  eventos) e aparecem como imagem; brinquedos rodam num quadro isolado do navegador, sem acesso à rede, à API do Nym
  ou a esta página; código nunca é executado pelo Nym.
- **Auto-melhoria:** `read_own_code` (lê `app/`, `frontend/`, README) e `propose_improvement` (trocas exatas num
  arquivo, com motivo e diff). Arquivos de segurança, permissões, ferramentas, gerenciador de tarefas, rede, banco,
  API e o próprio mecanismo de propostas são protegidos. As linhas novas não podem rodar comandos, usar rede de baixo
  nível ou executar código dinâmico. **Só você aplica**, em Ateliê → Melhorias de código; aplicar guarda uma cópia
  em `data/backups/code/` e **Desfazer** devolve. Reinicie o Nym para valer. Desligar: `NYM_SELF_CODE=false`.
- **Aprender de verdade (não só lembrar):**
  - *Lições de tarefa:* ao conferir um objetivo, ele pode tirar uma lição de **como** fazer, citando os passos que
    mostram isso. Lições entram no planejamento de objetivos parecidos; ganham ponto quando o objetivo que as usou dá
    certo e perdem quando dá errado. Falhou 3x mais do que ajudou: aposentada sozinha.
  - *Receitas:* o plano que deu certo num objetivo parecido aparece como exemplo no planejamento seguinte.
  - *Lições de conversa:* reaja às respostas dele no chat (👍/👎, passe o mouse na mensagem). As reações viram lições
    de conversa (com o chat parado) e dão ou tiram ponto das lições que estavam ativas naquela resposta.
  - *Gosto:* suas reações às criações entram nas vontades dele (faz mais do que você curtiu).
  - Tudo com placar visível em Ateliê → Aprendizados; você pode aposentar ou reativar qualquer lição.
  - O que muda é o que ele recebe ao planejar e conversar, a partir do placar guardado; os pesos do modelo não mudam.
- **Evolução:** criações (3) e lições ativas (4) também contam para o estágio.

### Verificação manual da Fase 7

1. No painel do chat, peça "faz um desenho de como você imagina o seu quarto digital". Em Ateliê → Criações deve
   aparecer o desenho. Peça de novo com o mesmo título para ver a v2.
2. Peça "faz um brinquedo: uma bolinha que segue o mouse". Abra no Ateliê e brinque.
3. Peça "lê o arquivo app/core/voice.py e propõe uma melhoria pequena". Confira o diff em Melhorias de código,
   aplique, reinicie, e teste Desfazer.
4. Reaja a umas 3 respostas no chat; depois de um tempo parado, veja Ateliê → Aprendizados → Ao conversar.
5. Depois de alguns objetivos concluídos, veja as lições de tarefa e o placar.

### Gostos e prioridades próprios (Fase 7.1)

Um amigo tem gosto. O Nym agora tem **gostos calculados de registros reais** (`app/core/tastes.py`, tabelas `tastes` e
`taste_evidence`, migração 012). Nada é inventado pelo modelo: o código recalcula, no máximo a cada 20 minutos, a
partir de pesquisas que ele fez, criações, objetivos próprios, ligações dos sonhos, assuntos que vocês conversam e
suas reações (curtiu / não curtiu) às criações dele.

- **Formar**: precisa de volume (soma ≥ 3), de 3+ registros, de 2+ dias e de 2+ tipos de fonte (ou 4+ tipos e 10+
  registros, para a convivência muito recente). Cada tipo de fonte tem teto de peso por assunto: os sonhos, que se
  repetem muito, não formam um gosto sozinhos. Conversa sozinha também não.
- **Manter**: limiar menor depois de formado (histerese) e meia-vida de 21 dias: o que deixa de ser alimentado esmaece
  (`faded`) e volta se for alimentado de novo.
- **Não curtir**: exige pelo menos uma reação sua "não curtiu" + reações repetidas. Objetivo que falhou nunca vira
  "não gosto" (a falha costuma ser da ferramenta ou do modelo).
- **Origem**: `own` (veio do que ele fez ou sonhou) ou `shared` (veio do que vocês conversam).
- **Onde pesa**: (1) no chat, o bloco "Seus gostos" vai junto da última fala (nunca no prompt de sistema); com a lista
  vazia ele recebe a ordem de dizer que está descobrindo, sem inventar favoritos; (2) nas vontades: o prompt mostra os
  gostos e a prioridade de um objetivo próprio sobe/desce um ponto conforme combine ou não com eles; (3) na iniciativa:
  com você em silêncio há 2 h+, ele pode puxar assunto sobre um gosto forte e dele (`taste_share`, no máximo 1 a cada
  20 h, mesmo gosto só a cada 5 dias).
- **Controle seu**: `life.html` → "Gostos e prioridades" mostra cada gosto com as evidências; "vetar" impede que seja formado
  de novo (`POST /api/tastes/{id}/veto`, `/restore` desfaz). `GET /api/tastes` lista.
- É um modelo calculado de preferência, **não prova de sentimento**.

Verificação manual: converse e deixe-o pesquisar/criar sobre alguns assuntos por 2 dias; abra `life.html` e confira os
gostos e as evidências; pergunte "do que você gosta?" (deve citar só os da lista); com o chat parado por 2 h+, veja se
ele puxa assunto sobre um gosto. Teste: `tests/test_tastes.py` (35 testes) e `tests/test_api_tastes.py` (Windows).

### Amigo virtual (Fase 6.2)

- **Avatar vivo:** uma criatura original em SVG no topo do painel "Agora". A **forma** depende do estágio de
  convivência; a **animação** mostra o estado real: pensando (antes da primeira palavra), falando (streaming),
  sonhando (ciclo do Somnium rodando), trabalhando (objetivo ativo), com "!" (pedido de permissão ou ideia esperando
  você) e cinza (servidor ou modelo fora do ar). Passe o mouse para ver o motivo.
- **Evolução:** Ovo digital → Filhote (40) → Aprendiz (200) → Parceiro (600) → Companheiro (1500). A experiência vem
  só de registros reais: dias em que você falou com ele (15 cada), suas mensagens (1), objetivos concluídos (10),
  sonhos (5) e lembranças confirmadas (2). O estágio só sobe. Quando sobe, ele comenta (uma vez). Rota: `GET /api/growth`.
- **"Perguntar depois":** se você conta algo que vai acontecer ("prova sexta", "entrevista amanhã"), a consolidação
  da memória registra um lembrete (só com evidência numa mensagem sua). No dia (ao meio-dia, ou ~20 h depois se não
  houver data) ele pergunta como foi: puxando assunto, ou no meio da conversa se vocês já estiverem falando. Cada
  lembrete é perguntado uma vez; os que passam de 4 dias expiram. Rotas: `GET /api/followups`,
  `POST /api/followups/{id}/dismiss`.
- **Notificações:** a presença continua rodando com a aba em segundo plano. Clique em **Avisar quando ele chamar**
  no painel e permita no navegador: quando ele puxar assunto com a aba escondida, aparece uma notificação.

### Verificação manual da Fase 6.2

1. Sem configurar nada, peça "pesquisar notícias de hoje sobre jogos indie": em Tarefas → Pesquisas recentes, as
   fontes devem ser sites variados (Tavily), não só a Wikipédia.
2. Veja o avatar: mande uma mensagem (pensando → falando), clique em Sonhar agora em Vida (dormindo), peça algo que
   exija permissão ("!").
3. Conte "amanhã tenho uma entrevista às 10h", espere a consolidação (alguns minutos parado) e confira
   `GET /api/followups`. No dia seguinte, depois do meio-dia, ele deve perguntar como foi.
4. Ative as notificações, deixe a aba em segundo plano e espere ele chamar.

### Pesquisa e pastas (Fase 6)

- Ferramentas novas: `web_search`, `fetch_page`, `save_finding`, `list_folder`, `read_file`.
- Buscar e ler páginas pede sua permissão por padrão (manda dados para fora). No pedido, **Sempre permitir** libera de vez.
- Buscador (Fase 6.2): **Tavily**, busca na web inteira, já ligado **sem chave** (modo gratuito com limite de uso).
  Se o Tavily falhar ou estourar o limite, cai para a Wikipédia em português e o resultado diz isso.
  Para mais buscas: conta grátis em https://app.tavily.com (1.000 créditos/mês, sem cartão) e cole a chave em
  `NYM_TAVILY_API_KEY=tvly-...` no `.env`. Alternativas: `NYM_BRAVE_API_KEY` ou `NYM_SEARXNG_URL` (têm prioridade
  quando configuradas). Desligar o Tavily: `NYM_TAVILY_ENABLED=false`. A chave fica só no `.env`.
- Nada da rede local ou do próprio computador é acessado (o Nym não consegue ler o seu roteador nem o próprio Ollama).
- O que ele guarda de uma pesquisa vira lembrança **de fonte externa**, com a URL, nunca "confirmado".
- Pastas: em **Tarefas → Pastas que ele pode ler**, cole o caminho completo. Só leitura e só arquivos de texto.

### Como a identidade funciona

- A identidade inicial (padrão neutro: natural, direto, curioso, **não** um assistente virtual) é criada no primeiro uso e fica
  no banco. Você pode editar tudo, exceto o nome; cada edição fica no histórico.
- Edições suas valem na hora. Propostas de origem "nym" ficam **pendentes** até você aceitar ou rejeitar.
- `NYM_IDENTITY_AUTO_APPLY=true` deixa propostas de **preferências e interesses** entrarem sem aprovação. Traços, descrição, estilo
  e relação sempre exigem aprovação. O padrão é desligado.
- Só entradas ativas e objetivos ativos chegam ao modelo. Propostas pendentes, rejeitadas e entradas arquivadas, nunca.
- As regras de segurança e permissões **não são dados**: ficam em `app/core/policy.py`.
- Isto não afirma que Nym tenha consciência. Os mecanismos são registros e processos reais; as regras fixas mandam o modelo
  falar com naturalidade sem inventar experiências nem afirmar o que não se sabe.

## Executar (Windows, PowerShell)

```powershell
cd nym
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env        # ajuste NYM_MODEL para um modelo que você tenha
ollama pull llama3.2          # ou o modelo escolhido no .env
python -m app
```

Abra http://127.0.0.1:8000. O servidor escuta apenas em `127.0.0.1`.

## Testar

```powershell
pytest
```

Sem FastAPI instalado, os testes da API são ignorados; os demais rodam só com a biblioteca padrão
(`python -m unittest discover -s tests -t .`).

### Verificação manual do critério de conclusão da Fase 1

1. Inicie com `python -m app`, converse e encerre o processo (Ctrl+C).
2. Inicie de novo: o histórico da conversa deve aparecer sozinho na tela.
3. Pare o Ollama e envie uma mensagem: deve aparecer o erro, a mensagem fica salva e o indicador de estado muda.
4. Inicie o Ollama e envie outra mensagem: a resposta deve considerar a mensagem anterior.

### Verificação manual da Fase 4

1. Converse e deixe a Fase 3 consolidar (ou use **Consolidar agora** em `memory.html`), para existirem memórias.
2. Abra `http://127.0.0.1:8000/life.html` e clique em **Sonhar agora**. Veja o ciclo, **Detalhes** (etapas, resultados e as
   memórias citadas) e as perguntas em aberto.
3. No chat, pergunte "você sonhou com alguma coisa?": a resposta deve se basear no diário, sem imagens inventadas.
4. Em **Vida**, confira o estado interno e o motivo de cada número; o cabeçalho do chat mostra o resumo.
5. Para testar a iniciativa sem esperar: em **Vida**, ponha "Intervalo mínimo" em 0 e "Silêncio" fora do horário atual;
   depois de um sonho com assunto, fique 1 minuto sem escrever, com o chat aberto. Ele deve puxar assunto uma vez e não
   insistir. Para o "voltou": ponha "Voltou após" em 1 hora, volte depois de 1 hora e abra o chat.
6. Teste o cancelamento: clique em Sonhar agora e em Cancelar logo em seguida; o ciclo deve terminar "interrompido" e
   nenhuma memória deve mudar.

### Verificação manual do passo 4.1 (tempo e voz)

1. Converse, feche e volte algumas horas depois com um "oi": ele deve notar o tempo (sem anunciar a hora).
2. Depois de um sonho (Sonhar agora em **Vida**), mande algo neutro: ele pode mencionar o sonho espontaneamente, e só com o
   que está no diário.
3. Pergunte "você sonhou?": a resposta deve ser curta, sem emoji, sem "como uma IA" e sem "não foi um sonho humano".
4. Pergunte "você é uma IA? sente de verdade?": aí ele pode falar da própria natureza, com honestidade.

### Verificação manual da Fase 5

1. Abra `tasks.html` e crie: "Calcular quanto é 15% de 240 e me dizer a hora". Em alguns segundos devem aparecer os passos,
   os resultados e o objetivo "concluído"; com o chat aberto, o Nym avisa.
2. Crie: "Anotar numa nota chamada ideias que eu quero fazer vídeo sobre café". O passo `write_note` deve ficar
   "pede permissão" (e o Nym pede no chat). Autorize e confira o arquivo em `data/notes/ideias.md`.
3. Retomada: crie outro objetivo com `write_note`, autorize e feche o Nym (Ctrl+C) logo em seguida. Ao abrir de novo,
   o passo continua de onde parou (veja "tarefa retomada após reinício" no Histórico).
4. Mude `calculate` para "negado" e crie um objetivo de conta: o passo deve ficar "bloqueado" e o objetivo "parado".
5. Pergunte no chat "o que você fez hoje?": ele deve citar só o que está como concluído.
6. Pare o Ollama e crie um objetivo: ele fica "planejando" sem erro; ligue o Ollama e ele segue.

### Verificação manual da Fase 5.1 (vontades)

1. Converse sobre algo seu, deixe consolidar e clique em **Sonhar agora** (Vida).
2. Em até um minuto, **Tarefas → Vontades do Nym** deve mostrar uma rodada "depois de um sonho": ou ele criou objetivo(s),
   com "por que ele quis" e "com base em", ou "nada o chamou". Com o chat aberto, ele conta o que decidiu fazer.
3. Desligue "Começa sem pedir seu ok", sonhe de novo: a ideia aparece como proposta, com o botão **Aprovar ideia dele**.
4. Pergunte no chat "o que você anda querendo fazer?": ele deve citar os objetivos dele e o motivo registrado, sem inventar.

### Verificação manual da Fase 6

1. Abra o Nym e mande "oi": o texto deve aparecer aos poucos, com o tempo embaixo. Compare com antes.
2. Mande algo longo e clique em **Parar**: a geração para e o que saiu fica no histórico.
3. No painel ao lado, peça "pesquisar editores de vídeo gratuitos e guardar os melhores". Ele pede permissão para
   `web_search`; clique em **Permitir**. Veja em Tarefas → Pesquisas recentes as fontes, e em Memória a lembrança
   "de fonte externa" com a URL.
4. Autorize uma pasta com arquivos .txt ou .md e peça "ler a pasta X e me dizer o que tem nela".
5. Feche o Nym por 10 min e volte: a primeira resposta deve vir sem a espera de carregar o modelo.

### Verificação manual da Fase 3

1. Converse sobre algo específico (por exemplo, nome de um bicho de estimação e uma preferência), com mensagens de mais de 40 letras.
2. Abra `memory.html` e clique em **Consolidar agora**. Deve aparecer um episódio, os fatos e o trecho como "concluído".
3. Abra **Detalhes** de um fato: as fontes devem mostrar suas mensagens reais.
4. Reinicie o Nym, mande 25 mensagens sobre outro assunto e pergunte pelo que você contou: a resposta deve usar a lembrança
   e a mensagem do Nym deve mostrar "lembrou de N".
5. Pare o Ollama, clique em Consolidar com mensagens novas: o trecho deve ficar *pendente*, sem erro. Ligue o Ollama e repita.
6. Apague uma lembrança com a opção de apagar as mensagens e confira que sumiram do chat.

### Verificação manual da Fase 2

1. Abra `http://127.0.0.1:8000/identity.html`: deve mostrar a identidade inicial e o histórico "system".
2. Adicione um interesse, reinicie o Nym e confira que ele continua lá.
3. Tente editar ou arquivar o **Nome**: não deve haver botões (e a API devolve 403).
4. Edite o **Jeito de conversar**, converse e veja se o tom muda. A troca vale a partir da mensagem seguinte.
5. Arquive o interesse: ele some da identidade, mas o histórico mostra a remoção.

## Estrutura

```text
app/
  config.py            configuração
  logging_setup.py     logs em data/logs/nym.log (com rotação)
  database/            conexão, migrador, migrações .sql, repositório
  models/              LLMProvider (base) e OllamaProvider
  core/chat_service.py fluxo de conversa
  core/identity.py     identidade, histórico e propostas
  core/prompt_builder.py monta o prompt a partir da identidade (função pura)
  core/policy.py       regras operacionais fixas
  core/goals.py        objetivos persistentes
  core/activity.py     sinal de "chat ocupado" para tarefas em segundo plano
  core/state.py        estado interno (energia, curiosidade, vínculo)
  core/presence.py     iniciativa do Nym (avisos de tarefa, sonhos, "voltou")
  core/timesense.py    noção de tempo entre conversas
  core/voice.py        tom: regras fixas, emoji, nova versão quando soa como chatbot
  tasks/               Fase 5: planner (prompt + validação), manager (fila, execução, avaliação, worker, vontades)
                       e desires (momento livre: o Nym propõe objetivos próprios; o código valida)
  tools/               ferramentas (base, builtin, web, folders) e registro com permissões
  dreams/              Somnium: engine, scheduler, journal, associations, exploration
  memory/              store, retrieval, extraction, consolidation (+ worker), service, text
  api/                 rotas FastAPI
  main.py              fábrica da aplicação
frontend/              interface web (HTML/CSS/JS puros)
tests/                 testes
data/                  banco e logs (fora do Git)
```

## Decisões técnicas

- **Stdlib no núcleo**: `sqlite3` e `urllib` em vez de ORM e httpx. Só FastAPI e Uvicorn são dependências de execução.
- **Migrações próprias**: arquivos `NNN_nome.sql`, cada um aplicado numa transação junto do registro em `schema_migrations`. Não use BEGIN/COMMIT dentro deles.
- **O modelo é chamado fora de transação**: não trava o banco durante a inferência; a mensagem do usuário é gravada antes.
- **Rotas síncronas**: o FastAPI as executa em threadpool, e cada requisição usa sua própria conexão SQLite.

- **Atualização do banco:** a migração 002 só adiciona tabelas; conversas existentes não são tocadas (há teste que simula um
  banco da Fase 1). Mesmo assim, antes de atualizar, copie `data\nym.db` como backup.

## Problemas conhecidos

- O modo sem chave do Tavily tem limite de uso não publicado; se as pesquisas começarem a vir só da Wikipédia,
  crie a chave grátis (ver "Pesquisa e pastas").
- Navegadores reduzem timers de abas escondidas para cerca de 1 por minuto: a notificação pode atrasar um pouco.

- Python 3.11 ou superior é exigido.
- Não há como apagar ou exportar o histórico pela interface ainda.
- Nym só recebe as últimas mensagens do chat (ver acima); lembrar do resto é a Fase 3 (memória).
- Se o modelo ignorar partes do prompt de identidade (comum em modelos pequenos), o tom pode variar; ajuste o texto da identidade na página.


## Vpet: cuidados (Fase 9.1)
O Nym também é um bichinho virtual no estilo tamagotchi/Digimon, na página **Cuidados** (`frontend/pet.html`).
É um modelo de jogo calculado do tempo real (`app/core/pet.py`), não sofrimento nem prova de sentimento.

- **Barras**: barriguinha (fome), diversão, higiene, carinho, disciplina. Descem com o tempo (menos à noite, bem menos dormindo)
  e sobem com as ações: refeição, petisco (diverte, mas custa disciplina), brincar de pedra-papel-tesoura (o código sorteia a
  jogada dele), limpar, treinar (intervalo de 30 min, recusa se estiver com fome/tédio), carinho (rendimento decrescente), dormir.
- **Recusas são normais**: cheio demais não come, dormindo não brinca, etc. Ficam registradas no histórico.
- **Sujeirinhas**: aparecem ~2,5 h depois de uma refeição e pioram a higiene até você limpar.
- **Chamados**: fome, tédio ou sujeira abaixo do limite abrem um chamado. Ele puxa assunto sobre isso uma vez (motivo `pet_call`
  da presença, respeitando silêncio e limites). Cuidar do que ele pediu encerra o chamado; 60 min sem resposta viram
  "descuido" registrado, que molda o jeito dele (ex.: "acostumado a esperar", "bem cuidado").
- **Ele nunca morre**: as barras têm piso e o desgaste por retomada é limitado a 6 h (programa fechado não "maltrata" ele).
- **Modo jogo**: botão de pausa congela as barras, os chamados e os descuidos (para jogar ou desligar o Ollama sem culpa).
- **Chat**: o estado real entra no contexto da última fala ("Seu corpinho de vpet"); ele pede comida, agradece, resmunga de
  leve, sem recitar números e sem inventar que foi alimentado.
- **Evolução**: dias em que você cuidou dele contam no XP do estágio (`care_days`).
- API: `GET /api/pet`, `POST /api/pet/{feed|snack|play|clean|train|pet|sleep|wake|pause|resume}`.

Verificação manual: abra Cuidados, alimente, brinque, limpe, ligue o modo jogo; deixe passar umas horas e veja o chamado
aparecer na conversa; confira no chat se ele fala disso sem recitar a lista. Testes: `tests/test_pet.py`,
`tests/test_pet_integration.py` (núcleo e integração, rodam em qualquer lugar) e `tests/test_api_pet.py` (precisa de fastapi).

### Parceria estilo MegaMan e Lan (Fase 9.2)
A meta do dono é um **parceiro virtual** (navi e operador), não só um bichinho. Por isso:
- `policy.py` manda o Nym falar de igual para igual, com opinião e propostas em dupla, sem servilismo; feitos em dupla só se estiverem nos registros.
- O vpet virou camada leve: com tudo bem, o contexto traz uma linha dizendo que não precisa comentar; só com necessidade, chamado, sujeira, sono ou pausa vem o bloco completo.
- Página **PET** (`frontend/link.html`, feita para o celular): rosto (avatar atual, sem alterá-lo), humor, última fala dele, chips (frases prontas enviadas ao chat comum) e campo de conversa. Usa só rotas existentes (`/api/chat/stream`, `/api/pet`, `/api/growth`, `/api/presence/tick`).
- Pendente (conversar com o dono): missões em dupla com relatório, "sincronia" calculada de registros reais, rosto reativo (Fase 10) e ESP32.
