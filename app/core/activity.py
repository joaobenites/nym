"""Sinaliza atividade do chat, para tarefas em segundo plano não competirem com o usuário."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator


class ActivityGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy = 0
        self._last: float | None = None

    @contextmanager
    def chat(self) -> Iterator[None]:
        with self._lock:
            self._busy += 1
        try:
            yield
        finally:
            with self._lock:
                self._busy -= 1
                self._last = time.monotonic()

    @contextmanager
    def work(self) -> Iterator[None]:
        """Trabalho de fundo que usa o modelo (planejar uma tarefa, por exemplo): marca ocupado para os outros
        trabalhadores esperarem, mas NÃO conta como conversa (não reinicia o tempo ocioso usado pelos sonhos)."""
        with self._lock:
            self._busy += 1
        try:
            yield
        finally:
            with self._lock:
                self._busy -= 1

    def is_busy(self) -> bool:
        with self._lock:
            return self._busy > 0

    def idle_seconds(self) -> float:
        with self._lock:
            if self._busy > 0:
                return 0.0
            return float("inf") if self._last is None else time.monotonic() - self._last
