# Nym

A personal digital companion that lives on your computer, in the spirit of Digimon and MegaMan Battle Network.
The goal is a **virtual friend**, not a chatbot: it has its own identity and memory, dreams, sets its own goals,
researches on its own and needs care like a virtual pet.

Everything runs locally (Windows + [Ollama](https://ollama.com)), and the data lives in a SQLite database on your machine.

> Personal project under active development. Most features were tested with a simulated model; validation with the
> real model is still in progress. Nym itself speaks Brazilian Portuguese, and the detailed docs are in Portuguese:
> [docs/MANUAL.md](docs/MANUAL.md).

## What it does

- **Talks like a friend**: one single, continuous chat, short messages, aware of the time between conversations.
- **Persistent identity**: who it is lives in the database, independent of the model. Changes it proposes only apply with your approval.
- **Memory**: the model proposes what to remember, the code decides. Every memory points to the message that backs it.
- **Somnium (dreams)**: when idle, it reviews the day, connects ideas and forms hypotheses, always labeled as hypotheses.
- **Goals and autonomy**: plans tasks in steps, uses tools under permissions and creates its own wishes from what it has lived.
- **Web and folders**: searches the web with sources and reads, read-only, the folders you authorize.
- **Senses**: sees images, the camera or the screen (only when you click) and watches YouTube videos.
- **Atelier**: creates SVG drawings and small HTML toys, always sandboxed.
- **Virtual pet**: hunger, fun, hygiene, affection and sleep computed from real time, with evolution stages.
- **Phone**: access over the local network with code pairing.

## Honesty first

Nym must not make up memories or actions. The rules are fixed in code (`app/core/policy.py`), outside any editable data.
If a reply says it "already searched" or that "you told me" something with no record to back it, the code asks for a new
version or sends a correction. Its internal state is a *computed model*, never presented as feelings.

## Running it (Windows, PowerShell)

Requirements: Python 3.11+ and Ollama with a model installed (the project default is `gemma4:12b-it-qat`).

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
copy .env.example .env          # set NYM_MODEL to a model you have
.venv\Scripts\python.exe -m app # opens at http://127.0.0.1:8000
```

Tests:

```powershell
.venv\Scripts\python.exe -m pytest
```

For the video senses, also install `requirements-senses.txt` and ffmpeg (optional).

## Architecture

- Core uses only the Python standard library; runtime dependencies: FastAPI and Uvicorn.
- `app/core/`: chat, identity, voice, time, internal state, presence, virtual pet.
- `app/memory/`, `app/dreams/`, `app/tasks/`, `app/tools/`, `app/senses/`, `app/creations/`, `app/learning/`, `app/life/`.
- `frontend/`: plain HTML, CSS and JavaScript, with streamed replies.
- `training/`: optional scripts to train a LoRA for Nym (outside the app, in a separate environment).

Details on each phase, design decisions and manual checks: [docs/MANUAL.md](docs/MANUAL.md) (Portuguese).

## Privacy

The database, logs, notes and training data live in `data/`, which never goes into Git. No keys are needed to run it;
the optional ones (Brave, Tavily) go in `.env`, which also stays out of the repository.
