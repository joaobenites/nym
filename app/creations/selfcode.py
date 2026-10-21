"""Auto-melhoria do Nym (Fase 7): ele LÊ o próprio código e PROPÕE mudanças. Quem aplica é sempre você.

O que é real: a proposta é um conjunto de trocas exatas ("troque este trecho por este") num arquivo do projeto,
com o motivo e o diff. O código confere tudo antes de guardar:
- só arquivos de `EDITABLE_DIRS`, fora de `PROTECTED` (regras de segurança, permissões, rede, banco, API, o próprio
  mecanismo de propostas e a página que as revisa ficam fora do alcance dele);
- cada trecho a trocar existe exatamente uma vez no arquivo;
- Python continua válido (ast.parse, que só lê o texto) e as linhas novas não trazem execução de comandos, rede de
  baixo nível ou execução dinâmica de código;
- o arquivo não mudou desde a proposta (hash) na hora de aplicar.

Aplicar (só pela API do usuário) guarda uma cópia do arquivo original em `data/backups/code/<id>/` e escreve de forma
atômica; Desfazer devolve a cópia. A mudança vale depois de reiniciar o Nym. O Nym nunca aplica nada sozinho e não
tem ferramenta para isso.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.database.repository import log_audit

READABLE = ("app", "frontend", "README.md", "CLAUDE.md")
EDITABLE_DIRS = ("app", "frontend")
EDITABLE_SUFFIXES = (".py", ".js", ".css", ".html")
PROTECTED = (
    "app/core/policy.py", "app/tools/", "app/tasks/manager.py", "app/config.py", "app/main.py",
    "app/__main__.py", "app/creations/", "app/api/", "app/database/", "frontend/atelier.html", "frontend/atelier.js",
    "app/senses/", "frontend/senses.js",   # Fase 8: câmera/tela só por clique; regras da visão
    "app/core/access.py", "frontend/login.html", "frontend/login.js", "frontend/shell.js",   # 8.1: acesso pela rede
)
FORBIDDEN_IN_NEW_LINES = {   # regex -> motivo (procura só nas linhas que a proposta acrescenta)
    r"\bsubprocess\b": "rodar programas", r"\bos\.(system|popen|exec\w*|spawn\w*)\b": "rodar comandos",
    r"\bsocket\b": "rede de baixo nível", r"\bctypes\b": "memória do sistema",
    r"(?<![\w.])(eval|exec|compile)\s*\(": "execução dinâmica", r"__import__|\bimportlib\b": "importação dinâmica",
    r"\bshutil\.rmtree\b": "apagar pastas", r"\b(os\.remove|os\.unlink)\b|\.unlink\(": "apagar arquivos",
    r"\bpickle\b": "dados executáveis", r"\bnew\s+Function\b": "execução dinâmica",
    r"\.innerHTML\b|\.outerHTML\b|insertAdjacentHTML": "HTML sem escapar",
}
MAX_EDITS = 5
MAX_FILE_CHARS = 200_000
MAX_READ_CHARS = 12_000
WINDOW_LINES = 120


class SelfCodeError(ValueError):
    pass


def _rel(root: Path, path: str) -> tuple[Path, str]:
    clean = path.strip().replace("\\", "/").lstrip("/")
    if not clean or ".." in clean.split("/"):
        raise SelfCodeError("Caminho inválido.")
    full = (root / clean).resolve()
    try:
        rel = full.relative_to(root).as_posix()
    except ValueError:
        raise SelfCodeError("Fora da pasta do projeto.") from None
    if "__pycache__" in rel or rel.startswith(".") or "/." in rel:
        raise SelfCodeError("Caminho não permitido.")
    return full, rel


def _readable(rel: str) -> bool:
    return any(rel == r or rel.startswith(r + "/") for r in READABLE)


def is_protected(rel: str) -> bool:
    return any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in PROTECTED)


def editable(rel: str) -> bool:
    return (any(rel.startswith(d + "/") for d in EDITABLE_DIRS) and rel.endswith(EDITABLE_SUFFIXES)
            and not is_protected(rel))


def _read(full: Path) -> tuple[bytes, str]:
    raw = full.read_bytes()
    if len(raw) > MAX_FILE_CHARS:
        raise SelfCodeError("Arquivo grande demais.")
    try:
        return raw, raw.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise SelfCodeError("Arquivo não é texto UTF-8.") from None


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_code(root: Path, path: str, focus: str | None = None) -> str:
    """Lista uma pasta ou mostra um arquivo (numerado). Com `focus`, mostra a região em volta do trecho."""
    full, rel = _rel(root, path or "app")
    if not _readable(rel) and rel not in ("", "."):
        raise SelfCodeError(f"Só leio {', '.join(READABLE)}.")
    if full.is_dir():
        items = []
        for p in sorted(full.iterdir()):
            if p.name.startswith(".") or p.name == "__pycache__":
                continue
            r = p.relative_to(root).as_posix()
            tag = "/" if p.is_dir() else ("" if editable(r) else "  (protegido: só leitura)")
            items.append(f"{r}{tag}")
        return "\n".join(items) or "(pasta vazia)"
    if not full.is_file():
        raise SelfCodeError("Arquivo não encontrado.")
    _raw, text = _read(full)
    lines = text.split("\n")
    start, end = 0, len(lines)
    if focus:
        idx = next((i for i, l in enumerate(lines) if focus.strip() and focus.strip() in l), None)
        if idx is None:
            raise SelfCodeError("Trecho de foco não encontrado no arquivo.")
        start, end = max(0, idx - WINDOW_LINES // 3), min(len(lines), idx + WINDOW_LINES)
    body = "\n".join(f"{i + 1:4d}| {lines[i]}" for i in range(start, end))
    header = f"{rel} ({len(lines)} linhas){'' if editable(rel) else ' — protegido: você pode ler, não pode propor mudança'}"
    if len(body) > MAX_READ_CHARS:
        body = body[:MAX_READ_CHARS] + "\n... (cortado: use focus para ver outra parte)"
    return header + "\n" + body


@dataclass
class Proposal:
    rel: str
    edits: list[dict[str, str]]
    new_text: str
    diff: str
    base_hash: str
    new_hash: str
    warnings: list[str]


def _newline(raw: bytes) -> str:
    return "\r\n" if b"\r\n" in raw else "\n"


def build(root: Path, path: str, edits: Any) -> Proposal:
    full, rel = _rel(root, path)
    if not editable(rel):
        raise SelfCodeError(f"{rel} não pode ser alterado por você (protegido ou fora de app/ e frontend/).")
    if not full.is_file():
        raise SelfCodeError("Arquivo não encontrado.")
    raw, text = _read(full)
    if not isinstance(edits, list) or not edits:
        raise SelfCodeError("Nenhuma troca proposta.")
    if len(edits) > MAX_EDITS:
        raise SelfCodeError(f"No máximo {MAX_EDITS} trocas por proposta.")
    new = text
    clean: list[dict[str, str]] = []
    added: list[str] = []
    for e in edits:
        if not isinstance(e, dict) or not isinstance(e.get("find"), str) or not isinstance(e.get("replace"), str):
            raise SelfCodeError("Troca mal formada (precisa de find e replace).")
        find, repl = e["find"].replace("\r\n", "\n"), e["replace"].replace("\r\n", "\n")
        if not find.strip():
            raise SelfCodeError("Trecho a trocar vazio.")
        if find == repl:
            raise SelfCodeError("Troca que não muda nada.")
        count = new.count(find)
        if count != 1:
            raise SelfCodeError(f"O trecho a trocar aparece {count} vez(es); precisa aparecer exatamente uma "
                                f"(copie do arquivo, com a indentação): {find.strip()[:60]!r}")
        new = new.replace(find, repl, 1)
        clean.append({"find": find, "replace": repl})
        old_lines = set(find.split("\n"))
        added += [l for l in repl.split("\n") if l not in old_lines]
    if rel.endswith(".py"):
        try:
            ast.parse(new)
        except SyntaxError as exc:
            raise SelfCodeError(f"Depois da troca o Python fica inválido (linha {exc.lineno}: {exc.msg}).") from None
    added_text = "\n".join(added)
    bad = [f"{m.group(0)} ({v})" for k, v in FORBIDDEN_IN_NEW_LINES.items() if (m := re.search(k, added_text))]
    if bad:
        raise SelfCodeError("Mudança recusada: as linhas novas usam " + ", ".join(bad) + ".")
    warnings = []
    if len(new) < len(text) * 0.6:
        warnings.append("remove muito código (mais de 40% do arquivo)")
    if re.search(r"^\s*(import|from)\s", added_text, re.M):
        warnings.append("adiciona import")
    nl = _newline(raw)
    new_bytes = new.replace("\n", nl).encode("utf-8")
    diff = "".join(difflib.unified_diff(text.splitlines(keepends=True), new.splitlines(keepends=True),
                                        fromfile=f"a/{rel}", tofile=f"b/{rel}", n=2))
    return Proposal(rel, clean, new, diff, sha(raw), sha(new_bytes), warnings)


def store(conn: sqlite3.Connection, p: Proposal, title: str, why: str, goal_id: int | None, now: str) -> int:
    for r in conn.execute("SELECT diff FROM code_proposals WHERE status = 'proposed' AND file = ?", (p.rel,)):
        if r["diff"] == p.diff:
            raise SelfCodeError("Já existe uma proposta igual esperando decisão.")
    cur = conn.execute(
        "INSERT INTO code_proposals (title, why, file, edits, diff, base_hash, new_hash, warnings, status, goal_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)",
        (title, why, p.rel, json.dumps(p.edits, ensure_ascii=False), p.diff, p.base_hash, p.new_hash,
         json.dumps(p.warnings, ensure_ascii=False), goal_id, now))
    return int(cur.lastrowid)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".nym-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _row(conn: sqlite3.Connection, pid: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM code_proposals WHERE id = ?", (pid,)).fetchone()
    if row is None:
        raise LookupError("Proposta não encontrada.")
    return row


def apply(conn: sqlite3.Connection, root: Path, backups: Path, pid: int, now: str) -> dict[str, Any]:
    """Só chamado pela API do usuário. Recalcula tudo a partir do arquivo ATUAL antes de escrever."""
    row = _row(conn, pid)
    if row["status"] != "proposed":
        raise SelfCodeError(f"A proposta está '{row['status']}', não dá para aplicar.")
    full, rel = _rel(root, row["file"])
    if not editable(rel):
        raise SelfCodeError("Arquivo protegido.")
    raw, _ = _read(full)
    if sha(raw) != row["base_hash"]:
        conn.execute("UPDATE code_proposals SET status = 'stale', decided_at = ? WHERE id = ?", (now, pid))
        raise SelfCodeError("O arquivo mudou desde a proposta; ela ficou desatualizada.")
    p = build(root, rel, json.loads(row["edits"]))     # mesmas validações de novo
    data = p.new_text.replace("\n", _newline(raw)).encode("utf-8")
    if sha(data) != row["new_hash"]:
        raise SelfCodeError("O resultado não bate com a proposta revisada.")
    folder = backups / str(pid)
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / Path(rel).name
    backup.write_bytes(raw)
    _atomic_write(full, data)
    conn.execute("UPDATE code_proposals SET status = 'applied', backup_path = ?, decided_at = ? WHERE id = ?",
                 (str(backup), now, pid))
    log_audit(conn, "selfcode.applied", {"proposal_id": pid, "file": rel, "by": "user"})
    return {"id": pid, "status": "applied", "file": rel, "restart_needed": True}


def revert(conn: sqlite3.Connection, root: Path, pid: int, now: str) -> dict[str, Any]:
    row = _row(conn, pid)
    if row["status"] != "applied" or not row["backup_path"]:
        raise SelfCodeError("Só dá para desfazer uma proposta aplicada.")
    full, rel = _rel(root, row["file"])
    raw = full.read_bytes()
    if sha(raw) != row["new_hash"]:
        raise SelfCodeError("O arquivo mudou depois de aplicar; desfaça à mão (a cópia está em " + row["backup_path"] + ").")
    backup = Path(row["backup_path"])
    if not backup.is_file():
        raise SelfCodeError("A cópia de segurança sumiu.")
    _atomic_write(full, backup.read_bytes())
    conn.execute("UPDATE code_proposals SET status = 'reverted', decided_at = ? WHERE id = ?", (now, pid))
    log_audit(conn, "selfcode.reverted", {"proposal_id": pid, "file": rel, "by": "user"})
    return {"id": pid, "status": "reverted", "file": rel, "restart_needed": True}


def reject(conn: sqlite3.Connection, pid: int, now: str) -> dict[str, Any]:
    row = _row(conn, pid)
    if row["status"] != "proposed":
        raise SelfCodeError("Só dá para recusar uma proposta pendente.")
    conn.execute("UPDATE code_proposals SET status = 'rejected', decided_at = ? WHERE id = ?", (now, pid))
    log_audit(conn, "selfcode.rejected", {"proposal_id": pid, "by": "user"})
    return {"id": pid, "status": "rejected"}


def listing(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    out = []
    for r in conn.execute("SELECT * FROM code_proposals ORDER BY id DESC LIMIT ?", (limit,)):
        d = dict(r)
        d["edits"] = json.loads(d["edits"])
        d["warnings"] = json.loads(d["warnings"])
        out.append(d)
    return out
