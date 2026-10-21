# Nym

Um companheiro digital pessoal que vive no seu computador, no espírito de Digimon e MegaMan Battle Network.
A ideia é um **amigo virtual**, não um chatbot: ele tem identidade e memória próprias, sonha, cria objetivos,
pesquisa sozinho e precisa de cuidados como um vpet.

Tudo roda localmente (Windows + [Ollama](https://ollama.com)), e os dados ficam num SQLite na sua máquina.

> Projeto pessoal em desenvolvimento ativo. A maior parte das funções foi testada com modelo simulado; a validação
> com o modelo real ainda está em andamento. O estado de cada parte está em [docs/MANUAL.md](docs/MANUAL.md).

## O que ele faz

- **Conversa como amigo**: um chat único e contínuo, mensagens curtas, com noção do tempo entre uma conversa e outra.
- **Identidade persistente**: quem ele é fica no banco, independente do modelo. Mudanças que ele propõe só valem com o seu ok.
- **Memória**: o modelo propõe o que lembrar, o código decide. Toda lembrança aponta para a mensagem que a sustenta.
- **Somnium (sonhos)**: quando está ocioso, revisa o dia, associa ideias e forma hipóteses, sempre marcadas como hipóteses.
- **Objetivos e autonomia**: planeja tarefas em passos, usa ferramentas com permissões e cria vontades próprias a partir do que viveu.
- **Internet e pastas**: pesquisa na web com fontes e lê, só para leitura, as pastas que você autorizar.
- **Sentidos**: vê imagens, a câmera ou a tela (só quando você clica) e assiste vídeos do YouTube.
- **Ateliê**: cria desenhos SVG e pequenos brinquedos HTML, sempre em sandbox.
- **Vpet**: fome, diversão, higiene, carinho e sono calculados do tempo real, com evolução em estágios.
- **Celular**: acesso pela rede local com pareamento por código.

## Honestidade antes de tudo

O Nym não pode inventar lembranças nem ações. As regras ficam fixas no código (`app/core/policy.py`), fora de qualquer
dado editável. Se a resposta diz que ele "já pesquisou" ou que "você me contou" algo sem registro que sustente, o código
pede uma nova versão ou manda uma correção. O estado interno dele é um *modelo calculado*, nunca apresentado como sentimento.

## Como rodar (Windows, PowerShell)

Requisitos: Python 3.11+ e o Ollama com um modelo instalado (o padrão do projeto é `gemma4:12b-it-qat`).

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
copy .env.example .env          # ajuste NYM_MODEL para o modelo que você tem
.venv\Scripts\python.exe -m app # abre em http://127.0.0.1:8000
```

Testes:

```powershell
.venv\Scripts\python.exe -m pytest
```

Para os sentidos de vídeo, instale também `requirements-senses.txt` e o ffmpeg (opcionais).

## Arquitetura

- Núcleo só com a biblioteca padrão do Python; dependências de execução: FastAPI e Uvicorn.
- `app/core/`: chat, identidade, voz, tempo, estado interno, presença, vpet.
- `app/memory/`, `app/dreams/`, `app/tasks/`, `app/tools/`, `app/senses/`, `app/creations/`, `app/learning/`, `app/life/`.
- `frontend/`: HTML, CSS e JavaScript puros, com streaming das respostas.
- `training/`: scripts opcionais para treinar um LoRA do Nym (fora do app, em ambiente separado).

Detalhes de cada fase, decisões e verificações manuais: [docs/MANUAL.md](docs/MANUAL.md).

## Privacidade

O banco, os logs, as notas e os dados de treino ficam em `data/`, que nunca vai para o Git. Nenhuma chave é necessária
para rodar; as opcionais (Brave, Tavily) vão no `.env`, que também fica fora do repositório.
