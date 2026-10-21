"""Configuração do Nym.

Ordem de precedência: variáveis de ambiente > arquivo .env > valores padrão.
Nenhum segredo é necessário na Fase 1.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ConfigError(ValueError):
    """Valor de configuração inválido."""


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "llama3.2"
    host: str = "127.0.0.1"
    port: int = 8000
    context_messages: int = 20
    context_chars: int = 12000
    request_timeout: float = 120.0
    log_level: str = "INFO"
    identity_chars: int = 3000
    identity_auto_apply: bool = False
    temperature: float = 0.8
    num_predict: int = 0  # 0 = sem limite de tokens por resposta
    num_ctx: int = 8192  # janela de contexto pedida ao Ollama (0 = padrão do Ollama)
    keep_alive: str = "30m"           # quanto tempo o Ollama mantém o modelo na GPU sem uso ("-1" = sempre)
    think: bool | None = False        # modo de pensar dos modelos que têm (mais lento); None = padrão do modelo
    warm_up: bool = True              # carrega o modelo ao iniciar o Nym
    episode_gap_minutes: int = 20     # pausa que separa um episódio do seguinte
    memory_chars: int = 1500          # orçamento de lembranças no prompt
    memory_worker: bool = True        # consolidação automática em segundo plano
    memory_interval_seconds: int = 60
    memory_idle_seconds: int = 20     # só consolida depois de tanto tempo sem conversa
    memory_max_attempts: int = 3
    # Somnium (sonhos)
    dreams_enabled: bool = True
    dream_idle_minutes: int = 30       # só sonha depois de tanto tempo sem conversa
    dream_min_interval_hours: int = 6  # intervalo mínimo entre ciclos
    dream_max_per_day: int = 3
    dream_max_seconds: int = 300       # limite de duração de um ciclo
    dream_max_model_calls: int = 2
    dream_max_memories: int = 30       # memórias revisadas por ciclo
    dream_schedule_hour: int = -1      # hora local de um ciclo diário agendado (-1 = desligado)
    # Presença (iniciativa do Nym)
    presence_enabled: bool = True
    presence_quiet_start: str = "23:00"
    presence_quiet_end: str = "08:00"
    presence_max_per_day: int = 0      # 0 = sem limite (escolha do dono na Fase 5)
    presence_min_gap_minutes: int = 0  # 0 = sem intervalo extra (há um piso técnico de 1 min)
    presence_welcome_back_hours: int = 4
    tasks_enabled: bool = True        # Fase 5: executar objetivos de tarefa em segundo plano
    task_interval_seconds: int = 10
    task_max_attempts: int = 3        # tentativas por tarefa (só para falhas que podem passar)
    task_timeout_seconds: int = 30    # tempo máximo de uma ferramenta
    goal_max_steps: int = 5           # passos por plano
    goal_max_replans: int = 6         # rodadas de passos depois de ver os resultados (Fase 8.2: passo a passo)
    goal_max_model_calls: int = 16    # chamadas ao modelo por objetivo (planejar + avaliar)
    goal_max_plan_attempts: int = 3   # respostas inválidas do modelo antes de pausar o objetivo
    web_timeout_seconds: int = 15     # Fase 6: tempo máximo de uma busca ou página
    search_lang: str = "pt"           # idioma da Wikipédia (buscador padrão, sem chave)
    searxng_url: str = ""             # opcional: seu SearXNG (ex.: http://127.0.0.1:8080)
    brave_api_key: str = ""           # opcional: chave da Brave Search API (fica só no .env)
    tavily_enabled: bool = True       # busca na web inteira pelo Tavily (sem chave: modo grátis com limite)
    tavily_api_key: str = ""          # opcional: chave grátis do Tavily (1.000 créditos/mês, sem cartão)
    chat_commitments: bool = True     # o que ele combina na conversa vira objetivo na hora
    voice_retry: bool = True          # pede uma nova versão quando a resposta soa como chatbot
    allow_emoji: bool = False
    self_code: bool = True            # Fase 7: o Nym lê o próprio código e PROPÕE melhorias (aplicar é sempre seu)
    code_root: Path | None = None     # pasta do projeto (None = a pasta onde este arquivo está)
    lan: bool = False                 # aceitar o celular pela rede local (escuta em 0.0.0.0; exige parear o aparelho)
    # Fase 8: sentidos
    vision_model: str = ""            # modelo com visão no Ollama ("" = o mesmo do chat, se ele enxergar)
    keep_images: bool = False         # guardar as imagens vistas em data/perceptions/ (padrão: só a descrição)
    video_max_minutes: int = 90       # vídeos mais longos: só o começo da legenda e menos quadros
    video_frames: int = 4             # quadros do vídeo que ele olha (com yt-dlp + ffmpeg)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "nym.db"

    @property
    def project_root(self) -> Path:
        return (self.code_root or Path(__file__).resolve().parent.parent).resolve()

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @classmethod
    def load(
        cls,
        env_file: str | Path = ".env",
        environ: Mapping[str, str] | None = None,
    ) -> "Settings":
        environ = os.environ if environ is None else environ
        file_values = _load_dotenv(Path(env_file))

        def get(name: str, default: str) -> str:
            return environ.get(name, file_values.get(name, default))

        def get_int(name: str, default: int, minimum: int) -> int:
            raw = get(name, str(default))
            try:
                value = int(raw)
            except ValueError:
                raise ConfigError(f"{name} deve ser um inteiro, recebido: {raw!r}") from None
            if value < minimum:
                raise ConfigError(f"{name} deve ser >= {minimum}, recebido: {value}")
            return value

        def get_bool(name: str, default: bool) -> bool:
            raw = get(name, "true" if default else "false").strip().lower()
            if raw in ("1", "true", "yes", "sim", "on"):
                return True
            if raw in ("0", "false", "no", "nao", "não", "off"):
                return False
            raise ConfigError(f"{name} deve ser verdadeiro ou falso, recebido: {raw!r}")

        def get_float(name: str, default: float, minimum: float) -> float:
            raw = get(name, str(default))
            try:
                value = float(raw)
            except ValueError:
                raise ConfigError(f"{name} deve ser um número, recebido: {raw!r}") from None
            if value < minimum:
                raise ConfigError(f"{name} deve ser >= {minimum}, recebido: {value}")
            return value

        def get_hhmm(name: str, default: str) -> str:
            raw = get(name, default).strip()
            parts = raw.split(":")
            if len(parts) != 2 or not all(x.isdigit() for x in parts) or not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
                raise ConfigError(f"{name} deve ser HH:MM, recebido: {raw!r}")
            return f"{int(parts[0]):02d}:{int(parts[1]):02d}"

        schedule_hour = get_int("NYM_DREAM_SCHEDULE_HOUR", cls.dream_schedule_hour, -1)
        if schedule_hour > 23:
            raise ConfigError(f"NYM_DREAM_SCHEDULE_HOUR deve estar entre -1 e 23, recebido: {schedule_hour}")

        think_raw = get("NYM_THINK", "false").strip().lower()
        if think_raw in ("auto", "padrao", "padrão", ""):
            think = None
        else:
            think = get_bool("NYM_THINK", False)
        keep_alive = get("NYM_KEEP_ALIVE", cls.keep_alive).strip()
        if keep_alive and not (keep_alive.lstrip("-").isdigit() or keep_alive[:-1].isdigit() and keep_alive[-1] in "smh"):
            raise ConfigError(f"NYM_KEEP_ALIVE deve ser como 30m, 2h, 600 ou -1; recebido: {keep_alive!r}")

        if not get("NYM_SEARCH_LANG", cls.search_lang).strip().isalpha():
            raise ConfigError("NYM_SEARCH_LANG deve ser um código de idioma, como pt ou en.")
        searx = get("NYM_SEARXNG_URL", "").strip()
        if searx and not searx.startswith(("http://", "https://")):
            raise ConfigError("NYM_SEARXNG_URL deve começar com http:// ou https://")

        temperature = get_float("NYM_TEMPERATURE", cls.temperature, 0.0)
        if temperature > 2.0:
            raise ConfigError(f"NYM_TEMPERATURE deve ser <= 2, recebido: {temperature}")

        return cls(
            data_dir=Path(get("NYM_DATA_DIR", "data")).expanduser().resolve(),
            ollama_url=get("NYM_OLLAMA_URL", cls.ollama_url).rstrip("/"),
            model=get("NYM_MODEL", cls.model),
            host=get("NYM_HOST", "0.0.0.0" if get_bool("NYM_LAN", False) else cls.host),
            lan=get_bool("NYM_LAN", False),
            port=get_int("NYM_PORT", cls.port, 1),
            context_messages=get_int("NYM_CONTEXT_MESSAGES", cls.context_messages, 1),
            context_chars=get_int("NYM_CONTEXT_CHARS", cls.context_chars, 200),
            request_timeout=get_float("NYM_REQUEST_TIMEOUT", cls.request_timeout, 1.0),
            log_level=get("NYM_LOG_LEVEL", cls.log_level).upper(),
            identity_chars=get_int("NYM_IDENTITY_CHARS", cls.identity_chars, 500),
            identity_auto_apply=get_bool("NYM_IDENTITY_AUTO_APPLY", cls.identity_auto_apply),
            temperature=get_float("NYM_TEMPERATURE", cls.temperature, 0.0),
            num_predict=get_int("NYM_NUM_PREDICT", cls.num_predict, 0),
            num_ctx=get_int("NYM_NUM_CTX", cls.num_ctx, 0),
            keep_alive=keep_alive,
            think=think,
            warm_up=get_bool("NYM_WARM_UP", cls.warm_up),
            episode_gap_minutes=get_int("NYM_EPISODE_GAP_MINUTES", cls.episode_gap_minutes, 1),
            memory_chars=get_int("NYM_MEMORY_CHARS", cls.memory_chars, 0),
            memory_worker=get_bool("NYM_MEMORY_WORKER", cls.memory_worker),
            memory_interval_seconds=get_int("NYM_MEMORY_INTERVAL_SECONDS", cls.memory_interval_seconds, 1),
            memory_idle_seconds=get_int("NYM_MEMORY_IDLE_SECONDS", cls.memory_idle_seconds, 0),
            memory_max_attempts=get_int("NYM_MEMORY_MAX_ATTEMPTS", cls.memory_max_attempts, 1),
            dreams_enabled=get_bool("NYM_DREAMS_ENABLED", cls.dreams_enabled),
            dream_idle_minutes=get_int("NYM_DREAM_IDLE_MINUTES", cls.dream_idle_minutes, 0),
            dream_min_interval_hours=get_int("NYM_DREAM_MIN_INTERVAL_HOURS", cls.dream_min_interval_hours, 0),
            dream_max_per_day=get_int("NYM_DREAM_MAX_PER_DAY", cls.dream_max_per_day, 1),
            dream_max_seconds=get_int("NYM_DREAM_MAX_SECONDS", cls.dream_max_seconds, 5),
            dream_max_model_calls=get_int("NYM_DREAM_MAX_MODEL_CALLS", cls.dream_max_model_calls, 1),
            dream_max_memories=get_int("NYM_DREAM_MAX_MEMORIES", cls.dream_max_memories, 3),
            dream_schedule_hour=schedule_hour,
            presence_enabled=get_bool("NYM_PRESENCE_ENABLED", cls.presence_enabled),
            presence_quiet_start=get_hhmm("NYM_PRESENCE_QUIET_START", cls.presence_quiet_start),
            presence_quiet_end=get_hhmm("NYM_PRESENCE_QUIET_END", cls.presence_quiet_end),
            presence_max_per_day=get_int("NYM_PRESENCE_MAX_PER_DAY", cls.presence_max_per_day, 0),
            presence_min_gap_minutes=get_int("NYM_PRESENCE_MIN_GAP_MINUTES", cls.presence_min_gap_minutes, 0),
            presence_welcome_back_hours=get_int("NYM_PRESENCE_WELCOME_BACK_HOURS", cls.presence_welcome_back_hours, 1),
            tasks_enabled=get_bool("NYM_TASKS_ENABLED", cls.tasks_enabled),
            task_interval_seconds=get_int("NYM_TASK_INTERVAL_SECONDS", cls.task_interval_seconds, 1),
            task_max_attempts=get_int("NYM_TASK_MAX_ATTEMPTS", cls.task_max_attempts, 1),
            task_timeout_seconds=get_int("NYM_TASK_TIMEOUT_SECONDS", cls.task_timeout_seconds, 1),
            goal_max_steps=get_int("NYM_GOAL_MAX_STEPS", cls.goal_max_steps, 1),
            goal_max_replans=get_int("NYM_GOAL_MAX_REPLANS", cls.goal_max_replans, 0),
            goal_max_model_calls=get_int("NYM_GOAL_MAX_MODEL_CALLS", cls.goal_max_model_calls, 1),
            goal_max_plan_attempts=get_int("NYM_GOAL_MAX_PLAN_ATTEMPTS", cls.goal_max_plan_attempts, 1),
            web_timeout_seconds=get_int("NYM_WEB_TIMEOUT_SECONDS", cls.web_timeout_seconds, 1),
            search_lang=get("NYM_SEARCH_LANG", cls.search_lang).strip().lower() or "pt",
            searxng_url=get("NYM_SEARXNG_URL", "").strip(),
            brave_api_key=get("NYM_BRAVE_API_KEY", "").strip(),
            tavily_enabled=get_bool("NYM_TAVILY_ENABLED", cls.tavily_enabled),
            tavily_api_key=get("NYM_TAVILY_API_KEY", "").strip(),
            self_code=get_bool("NYM_SELF_CODE", cls.self_code),
            vision_model=get("NYM_VISION_MODEL", "").strip(),
            keep_images=get_bool("NYM_KEEP_IMAGES", cls.keep_images),
            video_max_minutes=get_int("NYM_VIDEO_MAX_MINUTES", cls.video_max_minutes, 1),
            video_frames=min(get_int("NYM_VIDEO_FRAMES", cls.video_frames, 0), 8),
            chat_commitments=get_bool("NYM_CHAT_COMMITMENTS", cls.chat_commitments),
            voice_retry=get_bool("NYM_VOICE_RETRY", cls.voice_retry),
            allow_emoji=get_bool("NYM_ALLOW_EMOJI", cls.allow_emoji),
        )
