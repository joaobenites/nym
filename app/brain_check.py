"""Teste de cérebro: compara modelos do Ollama nas tarefas REAIS do Nym, sem mexer no Nym de verdade.

Uso (PowerShell, na pasta do projeto, com o Ollama aberto):
    .venv\\Scripts\\python.exe -m app.brain_check qwen3.5:9b gemma4:12b-it-qat

Para cada modelo, numa CÓPIA do banco (o original não é tocado, nem com o Nym aberto):
- conversa: 3 mensagens pelo chat de verdade (identidade, memórias, voz), com o tempo de cada resposta;
- vontades: o prompt real de "o que eu quero fazer agora" e se o JSON passa na validação do código;
- plano: planejar um objetivo de exemplo com as ferramentas reais e se o plano é aceito;
- diário: escrever a página de um dia real recente e se ela passa na validação.
Nada aqui é "nota" de qualidade inventada: são tempos medidos e a validação que o próprio Nym usa. A qualidade da
conversa você julga lendo as respostas.
"""
from __future__ import annotations

import argparse
import dataclasses
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from app.config import Settings
from app.core.chat_service import ChatService
from app.core.identity import IdentityService
from app.core.state import StateService
from app.database.connection import connect
from app.database.migrator import initialize_database
from app.life import day as day_mod
from app.life import diary as diary_mod
from app.memory.service import MemoryService
from app.models.base import LLMProvider, ModelError, ModelUnavailableError
from app.models.ollama_provider import OllamaProvider
from app.tasks import desires, planner
from app.tools.registry import ToolRegistry

CHAT = ("oi, tudo bem?", "o que você anda fazendo?", "me conta uma coisa que você curtiu recentemente")
SAMPLE_GOAL = {"description": "Pesquisar sobre Digimon Tamers e salvar uma nota com o que achar",
               "completion_criteria": "Uma nota salva com fontes"}


def copy_data(src: Settings, dest: Path) -> None:
    """Cópia consistente do banco (API de backup do SQLite, segura com o Nym aberto) e das notas."""
    dest.mkdir(parents=True, exist_ok=True)
    if src.db_path.exists():
        # Atenção: "with sqlite3.connect()" NÃO fecha a conexão (só faz commit); no Windows o arquivo ficava travado.
        a, b = sqlite3.connect(src.db_path), sqlite3.connect(dest / src.db_path.name)
        try:
            a.backup(b)
        finally:
            a.close()
            b.close()
    notes = Path(src.data_dir) / "notes"
    if notes.is_dir():
        shutil.copytree(notes, dest / "notes")


def timed(fn: Callable[[], Any]) -> tuple[Any, float, str | None]:
    started = time.monotonic()
    try:
        return fn(), time.monotonic() - started, None
    except ModelUnavailableError as exc:
        return None, time.monotonic() - started, f"modelo indisponível: {exc}"
    except (ModelError, ValueError, planner.PlanError) as exc:
        return None, time.monotonic() - started, str(exc)


def check_model(base: Settings, model: str) -> dict[str, Any]:
    tmp = tempfile.TemporaryDirectory(prefix="nym-cerebro-")
    try:
        data = Path(tmp.name)
        copy_data(base, data)
        s = dataclasses.replace(base, data_dir=data, model=model)
        initialize_database(s.db_path)
        provider: LLMProvider = OllamaProvider(s.ollama_url, model, s.request_timeout, temperature=s.temperature,
                                               num_predict=s.num_predict, num_ctx=s.num_ctx, keep_alive=s.keep_alive,
                                               think=s.think)
        status = provider.check_availability()
        out: dict[str, Any] = {"model": model, "available": status.available, "detail": status.detail, "results": []}
        if not status.available:
            return out
        _, load_s, err = timed(provider.warm_up)
        out["load_s"] = round(load_s, 1)
        chat = ChatService(s, provider, IdentityService(s), MemoryService(s), state=StateService(s))
        for text in CHAT:
            res, secs, err = timed(lambda t=text: chat.send_message(t))
            out["results"].append(("conversa", secs, err is None, f"você: {text}\n      nym: {res.reply}" if res else err))
        registry = ToolRegistry()
        conn = connect(s.db_path)
        try:
            ctx = desires.gather_context(conn, "")
            ctx["notes"] = desires.notes_overview(data / "notes")
            raw, secs, err = timed(lambda: provider.chat(desires.build_messages(ctx, registry, 2), temperature=0.7,
                                                         json_mode=True).content)
            if err is None:
                r, _, err = timed(lambda: desires.parse_desires(raw, ctx, 2))
                detail = err or ("ideias: " + "; ".join(d.description for d in r.desires) if r.desires else "nenhuma ideia")
                if r and r.dropped:
                    detail += f" (descartadas: {'; '.join(r.dropped)})"
            out["results"].append(("vontades (JSON)", secs, err is None, detail if err is None else err))

            raw, secs, err = timed(lambda: provider.chat(planner.plan_messages(SAMPLE_GOAL, registry, 5), temperature=0.2,
                                                         json_mode=True).content)
            if err is None:
                plan, _, err = timed(lambda: planner.parse_plan(raw, registry, 5))
                ok = err is None and bool(plan.steps)
                detail = err or (" → ".join(f"{st.tool}" for st in plan.steps) if plan.steps else f"plano vazio: {plan.reason}")
                if plan and plan.dropped:
                    detail += f" (descartados: {'; '.join(plan.dropped)})"
            else:
                ok, detail = False, err
            out["results"].append(("plano (JSON)", secs, ok, detail))

            today = diary_mod.today_local(chat._clock(), None)
            for back in range(1, 8):
                d = today - timedelta(days=back)
                facts = day_mod.facts(conn, *day_mod.bounds(d, None))
                if not day_mod.is_empty(facts):
                    raw, secs, err = timed(lambda: provider.chat(diary_mod.build_messages(d, day_mod.render(facts)),
                                                                 temperature=0.6).content)
                    page = None
                    if err is None:
                        page, _, err = timed(lambda: diary_mod.parse(raw))
                    out["results"].append((f"diário {d.strftime('%d/%m')}", secs, err is None,
                                           f"{page[0]}\n      Amanhã: {page[1]}" if page else err))
                    break
        finally:
            conn.close()
        return out
    finally:
        try:
            tmp.cleanup()
        except OSError:   # Windows às vezes segura o arquivo um instante; é só uma pasta temporária
            pass


def report(results: list[dict[str, Any]]) -> str:
    lines = []
    for r in results:
        lines.append(f"\n=== {r['model']} ===")
        if not r["available"]:
            hint = ("abra o Ollama e rode de novo" if "conectar" in r["detail"].lower()
                    else f"confira com 'ollama list'; se faltar: ollama pull {r['model']}")
            lines.append(f"  indisponível: {r['detail']}  ({hint})")
            continue
        lines.append(f"  carregar na GPU: {r.get('load_s', '?')} s")
        for name, secs, ok, detail in r["results"]:
            lines.append(f"  [{'ok ' if ok else 'ERRO'}] {name}: {secs:.1f} s\n      {detail}")
    lines.append("\nResumo (tempo total / tarefas que passaram na validação):")
    for r in results:
        if r["available"]:
            total = sum(x[1] for x in r["results"])
            passed = sum(1 for x in r["results"] if x[2])
            lines.append(f"  {r['model']}: {total:.0f} s, {passed}/{len(r['results'])} ok")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compara modelos do Ollama nas tarefas reais do Nym (numa cópia do banco).")
    parser.add_argument("models", nargs="+", help="nomes no Ollama, ex.: qwen3.5:9b gemma4:12b-it-qat")
    args = parser.parse_args(argv)
    base = Settings.load()
    results = []
    for m in args.models:
        print(f"Testando {m}... (pode levar alguns minutos)", flush=True)
        results.append(check_model(base, m))
    text = report(results)
    print(text)
    out = Path(base.data_dir) / "logs" / f"cerebro-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"\nRelatório salvo em {out}")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
