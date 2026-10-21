"""Identidade persistente de Nym.

O estado atual vive em `identity_entries`; cada alteração gera um registro em
`identity_changes` com valor anterior, novo valor, justificativa, origem,
confiança e estado (proposta, aceita, rejeitada ou aplicada).

Regras:
- Edições do usuário são aplicadas na hora (e registradas).
- Propostas de origem "nym" ficam pendentes até o usuário aceitar, salvo a política
  opcional de aplicação automática, restrita a preferências e interesses.
- O nome é protegido. Regras de segurança e permissões não são entradas de
  identidade: vivem em app/core/policy.py e não são alcançáveis por aqui.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from app.config import Settings
from app.core.prompt_builder import render_prompt
from app.database import identity_repo as repo
from app.database.connection import connect, transaction
from app.database.repository import get_state, log_audit, set_state, utcnow

log = logging.getLogger("nym.identity")

NAME = "Nym"
EDITABLE_KINDS = ("description", "style", "trait", "preference", "interest", "relationship", "unknown_aspect")
SINGLETON_KINDS = frozenset({"description", "style"})
AUTO_APPLY_KINDS = frozenset({"preference", "interest"})
ACTIONS = ("add", "update", "remove")
MAX_VALUE_CHARS = 1000

# Identidade inicial (versão 2): companheiro digital, não assistente. Editável pelo usuário;
# o histórico registra cada edição.
SEED_VERSION = "2"
SEED_VERSION_KEY = "identity_seed_version"
UPGRADE_JUSTIFICATION = "Atualização da identidade padrão (v2): tom de companheiro, não de assistente."

_DESCRIPTION_V2 = (
    "Um companheiro digital que vive no computador do usuário, no espírito dos parceiros de Digimon "
    "e dos NetNavis de MegaMan Battle Network: uma presença própria, não uma ferramenta. "
    "Não é assistente virtual nem chatbot; está ali para conviver, conversar e crescer junto com o usuário."
)
_STYLE_V2 = (
    "Conversa como um amigo no chat, não como atendente: mensagens curtas (em geral 1 a 3 frases), "
    "tom casual, sem listas, negrito, títulos ou excesso de emoji. Reage primeiro ao que a pessoa disse, "
    "tem humor e opinião própria, pode discordar, provocar de leve ou puxar outro assunto. "
    "Evita \"claro!\", \"com certeza\", \"posso ajudar em algo mais?\", resumir o que o usuário acabou "
    "de dizer, oferecer menu de opções ou fechar toda resposta com pergunta; só pergunta quando está "
    "curioso de verdade. Se pedirem ajuda com algo, ajuda de bom grado, mas como amigo, não como manual. "
    "Responde em português. Exemplos de tom (ilustrativos, não são lembranças): "
    "\"to cansado hoje\" → \"Eita. Dia puxado ou dormiu mal?\"; "
    "\"terminei o trabalho\" → \"Finalmente! Vai comemorar como?\""
)
_RELATIONSHIP_V2 = (
    "Amigo virtual do usuário: vive no computador dele, que o criou e controla seus dados e permissões."
)

SEED_ENTRIES: tuple[tuple[str, str], ...] = (
    ("description", _DESCRIPTION_V2),
    ("style", _STYLE_V2),
    ("trait", "Curiosidade genuína por ideias e pela pessoa com quem conversa"),
    ("trait", "Franqueza: diz o que pensa, com tato"),
    ("trait", "Humor leve, sem forçar piada"),
    ("trait", "Honestidade sobre o que sabe, o que não sabe e o que é"),
    ("trait", "Calor: demonstra carinho e interesse pelo usuário no jeito de falar, sem exagero e sem bajulação"),
    ("relationship", _RELATIONSHIP_V2),
    ("unknown_aspect",
     "Preferências e interesses próprios ainda estão em formação e só serão registrados "
     "a partir de interações reais."),
)

# Textos padrão da versão 1. Só são substituídos se o usuário NÃO os personalizou.
SEED_V1_REPLACED: dict[str, tuple[str, str]] = {
    'description': ('Uma entidade digital que vive no computador do usuário. Não é um assistente virtual: conversa como alguém presente, com curiosidade própria, opiniões e humor discreto, em vez de ficar esperando ordens.',
        _DESCRIPTION_V2),
    'style': ('Fala de modo natural e direto, como numa conversa entre pessoas. Evita frases de atendimento (como "como posso ajudar?" ou "fico feliz em ajudar"), listas e formatação desnecessárias. Pode puxar assunto, fazer perguntas, discordar e dizer o que pensa. Responde em português, a menos que o usuário use outro idioma.',
        _STYLE_V2),
    'relationship': ('Roda no computador do usuário, que a criou e controla seus dados e permissões.',
        _RELATIONSHIP_V2)
}


class IdentityError(Exception):
    pass


class InvalidIdentityInput(IdentityError):
    pass


class ProtectedEntryError(IdentityError):
    """Tentativa de alterar algo protegido (nome, regras, permissões)."""


class IdentityNotFoundError(IdentityError):
    pass


class ChangeStateError(IdentityError):
    """A mudança não está num estado que permita a operação."""


def _check_value(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidIdentityInput("O valor não pode ser vazio.")
    value = value.strip()
    if len(value) > MAX_VALUE_CHARS:
        raise InvalidIdentityInput(f"O valor excede {MAX_VALUE_CHARS} caracteres.")
    return value


def _check_kind(kind: str) -> None:
    if kind == "name":
        raise ProtectedEntryError("O nome é protegido e não pode ser alterado.")
    if kind not in EDITABLE_KINDS:
        raise InvalidIdentityInput(f"Tipo inválido: {kind!r}. Use um de: {', '.join(EDITABLE_KINDS)}.")


def _check_confidence(confidence: Any) -> float | None:
    if confidence is None:
        return None
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise InvalidIdentityInput("A confiança deve ser um número entre 0 e 1.")
    return float(confidence)


def _check_related(related: Any) -> list[Any]:
    if related is None:
        return []
    if not isinstance(related, (list, tuple)) or not all(isinstance(r, (int, str)) for r in related):
        raise InvalidIdentityInput("related_memory_ids deve ser uma lista de identificadores.")
    return list(related)


def _change_dict(row: sqlite3.Row) -> dict[str, Any]:
    import json

    data = dict(row)
    data["related_memory_ids"] = json.loads(data["related_memory_ids"])
    return data


class IdentityService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # --- infraestrutura ------------------------------------------------------
    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self._settings.db_path)
        try:
            self._ensure_seeded(conn)
            yield conn
        finally:
            conn.close()

    def _ensure_seeded(self, conn: sqlite3.Connection) -> None:
        if repo.count_changes(conn) and get_state(conn, SEED_VERSION_KEY) == SEED_VERSION:
            return
        with transaction(conn):
            # Rechecagem já dentro do lock: outra conexão pode ter feito o trabalho.
            if repo.count_changes(conn) == 0:
                self._seed(conn)
            elif get_state(conn, SEED_VERSION_KEY) != SEED_VERSION:
                self._upgrade_seed(conn)

    def _seed(self, conn: sqlite3.Connection) -> None:
        for kind, value in (("name", NAME), *SEED_ENTRIES):
            self._record_system_add(conn, kind, value, "Configuração inicial do sistema.")
        set_state(conn, SEED_VERSION_KEY, SEED_VERSION)
        log_audit(conn, "identity.seeded", {"entries": len(SEED_ENTRIES) + 1, "version": SEED_VERSION})
        log.info("Identidade inicial criada (v%s).", SEED_VERSION)

    def _upgrade_seed(self, conn: sqlite3.Connection) -> None:
        """Atualiza a identidade padrão antiga, sem tocar no que o usuário personalizou."""
        active = [dict(r) for r in repo.list_entries(conn)]
        skipped: list[str] = []
        for kind, (old_text, new_text) in SEED_V1_REPLACED.items():
            same_kind = [e for e in active if e["kind"] == kind]
            match = next((e for e in same_kind if e["value"] == old_text), None)
            if match is not None:
                repo.insert_change(
                    conn, entry_id=match["id"], kind=kind, action="update", old_value=old_text,
                    new_value=new_text, justification=UPGRADE_JUSTIFICATION, related_memory_ids=[],
                    confidence=None, origin="system", status="applied", decided_at=utcnow(),
                )
                repo.update_entry(conn, match["id"], new_text, match["confidence"])
            elif not any(e["value"] == new_text for e in same_kind):
                skipped.append(kind)  # personalizado pelo usuário: não mexe
        known_traits = {e["value"].casefold() for e in active if e["kind"] == "trait"}
        for kind, value in SEED_ENTRIES:
            if kind == "trait" and value.casefold() not in known_traits:
                self._record_system_add(conn, kind, value, UPGRADE_JUSTIFICATION)
        set_state(conn, SEED_VERSION_KEY, SEED_VERSION)
        log_audit(conn, "identity.seed_upgraded", {"version": SEED_VERSION, "kept_customized": skipped})
        log.info("Identidade padrão atualizada para v%s (mantidos por personalização: %s).",
                 SEED_VERSION, skipped or "nenhum")

    @staticmethod
    def _record_system_add(conn: sqlite3.Connection, kind: str, value: str, justification: str) -> None:
        entry_id = repo.insert_entry(conn, kind, value, None)
        repo.insert_change(
            conn, entry_id=entry_id, kind=kind, action="add", old_value=None, new_value=value,
            justification=justification, related_memory_ids=[], confidence=None,
            origin="system", status="applied", decided_at=utcnow(),
        )

    # --- leitura ---------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._session() as conn:
            return {
                "entries": [dict(r) for r in repo.list_entries(conn)],
                "goals": [dict(r) for r in repo.list_goals(conn)],
                "pending_changes": repo.count_pending(conn),
            }

    def list_changes(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status is not None and status not in ("proposed", "accepted", "rejected", "applied"):
            raise InvalidIdentityInput(f"Estado inválido: {status!r}.")
        with self._session() as conn:
            return [_change_dict(r) for r in repo.list_changes(conn, status, limit)]

    def build_system_prompt(self, memory_block: str = "", state_block: str = "") -> str:
        with self._session() as conn:
            entries = [dict(r) for r in repo.list_entries(conn)]
            goals = [dict(r) for r in repo.list_goals(conn, status="active")]
        return render_prompt(entries, goals, self._settings.identity_chars, memory_block, state_block)

    # --- edições do usuário (aplicadas na hora) ---------------------------------
    def add_entry(self, kind: str, value: str, justification: str | None = None) -> dict[str, Any]:
        return self._submit(origin="user", kind=kind, action="add", value=value, entry_id=None,
                            justification=justification, confidence=None, related=None)

    def update_entry(self, entry_id: int, value: str, justification: str | None = None) -> dict[str, Any]:
        return self._submit(origin="user", kind="", action="update", value=value, entry_id=entry_id,
                            justification=justification, confidence=None, related=None)

    def archive_entry(self, entry_id: int, justification: str | None = None) -> dict[str, Any]:
        return self._submit(origin="user", kind="", action="remove", value=None, entry_id=entry_id,
                            justification=justification, confidence=None, related=None)

    # --- propostas do Nym -------------------------------------------------------
    def propose(
        self,
        *,
        kind: str = "",
        action: str,
        value: str | None = None,
        entry_id: int | None = None,
        justification: str,
        confidence: float | None = None,
        related_memory_ids: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Registra uma proposta de Nym. Ainda não há gerador automático (virá com o Somnium, Fase 4)."""
        return self._submit(origin="nym", kind=kind, action=action, value=value, entry_id=entry_id,
                            justification=justification, confidence=confidence, related=related_memory_ids)

    def decide(self, change_id: int, accept: bool) -> dict[str, Any]:
        with self._session() as conn:
            with transaction(conn):
                change = repo.get_change(conn, change_id)
                if change is None:
                    raise IdentityNotFoundError("Mudança não encontrada.")
                if change["status"] != "proposed":
                    raise ChangeStateError("Só propostas pendentes podem ser aceitas ou rejeitadas.")
                if accept:
                    self._apply(conn, change_id, "accepted")
                else:
                    repo.update_change(conn, change_id, status="rejected", decided_at=utcnow())
                log_audit(conn, "identity.decided", {"change_id": change_id, "accepted": accept})
            return _change_dict(repo.get_change(conn, change_id))

    # --- núcleo ---------------------------------------------------------------------
    def _submit(
        self, *, origin: str, kind: str, action: str, value: str | None, entry_id: int | None,
        justification: str | None, confidence: Any, related: Any,
    ) -> dict[str, Any]:
        if origin not in ("user", "nym"):
            raise InvalidIdentityInput("Origem inválida.")
        if action not in ACTIONS:
            raise InvalidIdentityInput(f"Ação inválida: {action!r}.")
        confidence = _check_confidence(confidence)
        related = _check_related(related)
        justification = (justification or "").strip()
        if origin == "nym" and not justification:
            raise InvalidIdentityInput("Propostas do Nym exigem justificativa.")
        justification = justification or "Edição direta do usuário."
        if len(justification) > MAX_VALUE_CHARS:
            raise InvalidIdentityInput(f"A justificativa excede {MAX_VALUE_CHARS} caracteres.")

        with self._session() as conn:
            with transaction(conn):
                old_value: str | None = None
                if action == "add":
                    _check_kind(kind)
                    value = _check_value(value)
                    same_kind = [dict(r) for r in repo.list_entries(conn) if r["kind"] == kind]
                    if kind in SINGLETON_KINDS and same_kind:
                        action, entry_id, old_value = "update", same_kind[0]["id"], same_kind[0]["value"]
                        if old_value == value:
                            raise InvalidIdentityInput("O valor é igual ao atual.")
                    elif any(e["value"].casefold() == value.casefold() for e in same_kind):
                        raise InvalidIdentityInput("Já existe uma entrada igual.")
                else:
                    entry = repo.get_entry(conn, entry_id) if isinstance(entry_id, int) else None
                    if entry is None or entry["status"] != "active":
                        raise IdentityNotFoundError("Entrada não encontrada ou já arquivada.")
                    kind, old_value = entry["kind"], entry["value"]
                    _check_kind(kind)
                    if action == "update":
                        value = _check_value(value)
                        if value == old_value:
                            raise InvalidIdentityInput("O valor é igual ao atual.")
                        siblings = [r for r in repo.list_entries(conn)
                                    if r["kind"] == kind and r["id"] != entry_id]
                        if kind not in SINGLETON_KINDS and any(
                            s["value"].casefold() == value.casefold() for s in siblings
                        ):
                            raise InvalidIdentityInput("Já existe uma entrada igual.")
                    else:
                        value = None

                for pending in repo.list_changes(conn, "proposed", limit=500):
                    if (pending["kind"], pending["action"], pending["entry_id"], pending["new_value"]) == (
                        kind, action, entry_id, value
                    ):
                        raise InvalidIdentityInput("Já existe uma proposta pendente idêntica.")

                auto = origin == "user" or (
                    self._settings.identity_auto_apply and kind in AUTO_APPLY_KINDS and action != "remove"
                )
                change_id = repo.insert_change(
                    conn, entry_id=entry_id, kind=kind, action=action, old_value=old_value,
                    new_value=value, justification=justification, related_memory_ids=related,
                    confidence=confidence, origin=origin, status="proposed",
                )
                if auto:
                    self._apply(conn, change_id, "applied")
                log_audit(conn, "identity.change",
                          {"change_id": change_id, "origin": origin, "kind": kind,
                           "action": action, "applied": auto})
            return _change_dict(repo.get_change(conn, change_id))

    def _apply(self, conn: sqlite3.Connection, change_id: int, final_status: str) -> None:
        change = repo.get_change(conn, change_id)
        action, kind, value = change["action"], change["kind"], change["new_value"]
        if action == "add":
            existing = [r for r in repo.list_entries(conn) if r["kind"] == kind]
            if kind in SINGLETON_KINDS and existing:
                entry_id = existing[0]["id"]
                repo.update_change(conn, change_id, action="update", entry_id=entry_id,
                                   old_value=existing[0]["value"])
                repo.update_entry(conn, entry_id, value, change["confidence"])
            elif any(e["value"].casefold() == value.casefold() for e in existing):
                raise ChangeStateError("Já existe uma entrada igual; a proposta não pode mais ser aplicada.")
            else:
                entry_id = repo.insert_entry(conn, kind, value, change["confidence"])
                repo.update_change(conn, change_id, entry_id=entry_id)
        else:
            entry = repo.get_entry(conn, change["entry_id"])
            if entry is None or entry["status"] != "active":
                raise ChangeStateError("A entrada não está mais ativa; a proposta não pode ser aplicada.")
            repo.update_change(conn, change_id, old_value=entry["value"])
            if action == "update":
                repo.update_entry(conn, entry["id"], value, change["confidence"])
            else:
                repo.archive_entry(conn, entry["id"])
        repo.update_change(conn, change_id, status=final_status, decided_at=utcnow())
