import mimetypes
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import access as access_api
from app.api import atelier as atelier_api
from app.api import chat as chat_api
from app.api import days as days_api
from app.api import health as health_api
from app.api import identity as identity_api
from app.api import memory as memory_api
from app.api import pet as pet_api
from app.api import senses as senses_api
from app.api import dreams as dreams_api
from app.api import life as life_api
from app.api import tasks as tasks_api
from app.config import Settings
from app.core import access
from app.core.access import AccessGuard
from app.database.connection import connect
from app.database.repository import utcnow
from app.core.activity import ActivityGate
from app.core.chat_service import ChatService
from app.creations.service import AtelierService
from app.core.goals import GoalService
from app.core.identity import IdentityService
from app.core.presence import PresenceService
from app.core.state import StateService
from app.dreams.engine import DreamEngine
from app.dreams.journal import DreamJournal
from app.dreams.scheduler import DreamScheduler
from app.database.migrator import initialize_database
from app.logging_setup import setup_logging
from app.memory.consolidation import Consolidator, MemoryWorker
from app.memory.service import MemoryService
from app.models.base import LLMProvider
from app.models.ollama_provider import OllamaProvider
from app.senses.vision import VisionService
from app.tasks.manager import TaskManager, TaskWorker
from app.tools.registry import ToolRegistry

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def create_app(settings: Settings | None = None, provider: LLMProvider | None = None) -> FastAPI:
    settings = settings or Settings.load()
    log = setup_logging(settings)
    applied = initialize_database(settings.db_path)
    if applied:
        log.info("Esquema atualizado: %s", ", ".join(applied))

    provider = provider or OllamaProvider(
        settings.ollama_url, settings.model, settings.request_timeout,
        temperature=settings.temperature, num_predict=settings.num_predict,
        num_ctx=settings.num_ctx, keep_alive=settings.keep_alive, think=settings.think,
    )

    gate = ActivityGate()
    identity = IdentityService(settings)
    memory = MemoryService(settings)
    consolidator = Consolidator(settings, provider)
    worker = MemoryWorker(consolidator, gate, settings.memory_interval_seconds, settings.memory_idle_seconds)
    state = StateService(settings)
    journal = DreamJournal(settings)
    journal.recover_stale()   # ciclo que ficou "em andamento" porque o programa foi encerrado vira "interrompido"
    dream_engine = DreamEngine(settings, provider, identity)
    dream_scheduler = DreamScheduler(dream_engine, journal, gate, settings)
    presence = PresenceService(settings, provider, identity, memory, state, gate)
    def make_provider(model: str) -> LLMProvider:   # modelo de visão separado (NYM_VISION_MODEL), mesmo Ollama
        return OllamaProvider(settings.ollama_url, model, settings.request_timeout, num_ctx=settings.num_ctx,
                              keep_alive=settings.keep_alive, think=settings.think)

    vision = VisionService(settings, provider, make_provider)
    tasks = TaskManager(settings, provider, ToolRegistry(), memory, gate, senses=vision)
    resumed = tasks.recover()   # tarefas que estavam rodando quando o programa fechou voltam para a fila
    if resumed:
        log.info("%s tarefa(s) retomada(s) após reinício.", resumed)
    task_worker = TaskWorker(tasks, settings.task_interval_seconds)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.warm_up:  # carrega o modelo já, em segundo plano: a primeira mensagem não espera o carregamento
            threading.Thread(target=provider.warm_up, name="nym-warm-up", daemon=True).start()
        if settings.memory_worker:
            worker.start()
            log.info("Consolidação de memória em segundo plano ativada.")
        if settings.dreams_enabled:
            dream_scheduler.start()
            log.info("Somnium (sonhos) ativado: ciclos quando o chat ficar parado.")
        if settings.tasks_enabled:
            task_worker.start()
            log.info("Tarefas ativadas: objetivos executados em segundo plano.")
        try:
            yield
        finally:
            worker.stop()
            dream_scheduler.stop()
            task_worker.stop()

    app = FastAPI(title="Nym", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.provider = provider
    app.state.identity = identity
    app.state.goals = GoalService(settings)
    app.state.memory = memory
    app.state.consolidator = consolidator
    app.state.memory_worker = worker
    app.state.state = state
    app.state.dreams = journal
    app.state.dream_engine = dream_engine
    app.state.dream_scheduler = dream_scheduler
    app.state.presence = presence
    app.state.tasks = tasks
    app.state.atelier = AtelierService(settings)
    app.state.task_worker = task_worker
    app.state.vision = vision
    app.state.chat_service = ChatService(settings, provider, identity, memory, gate, state, journal, tasks=tasks,
                                         senses=vision)

    guard = AccessGuard(lambda: connect(settings.db_path), utcnow)
    app.state.access = guard

    @app.middleware("http")
    async def lan_guard(request: Request, call_next):
        """No computador: entra direto (com Host local). Pela rede: só aparelho pareado (ver app/core/access.py)."""
        client = request.client.host if request.client else None
        path = request.url.path
        if access.is_loopback(client):
            if access.host_name(request.headers.get("host")) not in access.LOCAL_HOSTS:
                return JSONResponse({"detail": "Endereço não permitido."}, status_code=403)   # DNS rebinding
            return await call_next(request)
        if path in access.PUBLIC_PATHS:
            return await call_next(request)
        device = guard.device_for(request.cookies.get(access.COOKIE), client or "?")
        if device is None:
            if path.startswith("/api/") or path == "/health":
                return JSONResponse({"detail": "Este aparelho não está pareado com o Nym."}, status_code=401)
            return RedirectResponse("/login.html", status_code=303)
        request.state.device = device
        return await call_next(request)

    app.include_router(access_api.router)
    app.include_router(health_api.router)
    app.include_router(chat_api.router)
    app.include_router(identity_api.router)
    app.include_router(memory_api.router)
    app.include_router(dreams_api.router)
    app.include_router(life_api.router)
    app.include_router(tasks_api.router)
    app.include_router(atelier_api.router)
    app.include_router(senses_api.router)
    app.include_router(days_api.router)
    app.include_router(pet_api.router)
    mimetypes.add_type("application/manifest+json", ".webmanifest")   # "Adicionar à tela inicial" no celular
    if FRONTEND_DIR.is_dir():
        # Montado por último para não sobrescrever as rotas da API.
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    log.info("Nym %s iniciado (modelo: %s, dados em: %s)", __version__, provider.model, settings.data_dir)
    return app
