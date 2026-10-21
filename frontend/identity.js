"use strict";

const $ = (id) => document.getElementById(id);
const KIND = {
  name: "Nome", description: "Descrição", style: "Jeito de conversar", trait: "Traço",
  preference: "Preferência", interest: "Interesse", relationship: "Relação com o usuário",
  unknown_aspect: "Em aberto",
};
const ADDABLE = ["trait", "preference", "interest", "relationship", "unknown_aspect", "description", "style"];
const STATUS = { proposed: "proposta", accepted: "aceita", rejected: "rejeitada", applied: "aplicada" };
const GOAL_STATUS = { proposed: "proposto", active: "ativo", paused: "pausado", done: "concluído", cancelled: "cancelado" };

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...options,
  });
  let data = null;
  try { data = await res.json(); } catch { /* sem corpo */ }
  if (!res.ok) {
    throw new Error(Array.isArray(data?.detail) ? "Valor inválido." : (data?.detail || `Erro HTTP ${res.status}`));
  }
  return data;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;   // textContent: nunca interpreta HTML
  return node;
}

function button(label, handler) {
  const b = el("button", "", label);
  b.type = "button";
  b.addEventListener("click", () => run(handler));
  return b;
}

async function run(fn) {
  $("error").textContent = "";
  try { await fn(); } catch (err) { $("error").textContent = err.message; }
  await load();
}

function entryItem(e) {
  const item = el("div", "item");
  item.append(el("span", "kind", KIND[e.kind] || e.kind), el("div", "", e.value));
  if (e.kind !== "name") {
    const row = el("div", "row");
    row.append(
      button("Editar", async () => {
        const value = window.prompt("Novo valor:", e.value);
        if (value === null) return;
        await api(`/api/identity/entries/${e.id}`, { method: "PATCH", body: JSON.stringify({ value }) });
      }),
      button("Arquivar", async () => {
        if (!window.confirm("Arquivar esta entrada? Ela sai do contexto, mas o histórico permanece.")) return;
        await api(`/api/identity/entries/${e.id}`, { method: "DELETE" });
      }),
    );
    item.append(row);
  }
  return item;
}

function changeText(c) {
  const date = new Date(c.created_at).toLocaleString("pt-BR");
  const arrow = c.action === "remove" ? `removido: ${c.old_value}`
    : c.old_value ? `${c.old_value}  →  ${c.new_value}` : `novo: ${c.new_value}`;
  return `${date} · ${c.origin} · ${STATUS[c.status]} · ${KIND[c.kind] || c.kind}\n${arrow}\nMotivo: ${c.justification}`
    + (c.confidence != null ? `\nConfiança: ${c.confidence}` : "")
    + (c.related_memory_ids.length ? `\nMemórias: ${c.related_memory_ids.join(", ")}` : "");
}

async function load() {
  const [snap, pending, history, goals] = await Promise.all([
    api("/api/identity"), api("/api/identity/changes?status=proposed"),
    api("/api/identity/changes?limit=50"), api("/api/goals"),
  ]);

  const order = ["name", "description", "style", "trait", "relationship", "preference", "interest", "unknown_aspect"];
  const sorted = [...snap.entries].sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));
  $("entries").replaceChildren(...sorted.map(entryItem));

  $("pending").replaceChildren(...(pending.length ? pending.map((c) => {
    const item = el("div", "item pending");
    item.append(el("div", "", changeText(c)));
    item.style.whiteSpace = "pre-wrap";
    const row = el("div", "row");
    row.append(
      button("Aceitar", () => api(`/api/identity/changes/${c.id}/accept`, { method: "POST" })),
      button("Rejeitar", () => api(`/api/identity/changes/${c.id}/reject`, { method: "POST" })),
    );
    item.append(row);
    return item;
  }) : [el("p", "kind", "Nenhuma proposta pendente.")]));

  $("history").replaceChildren(...history.map((c) => {
    const item = el("div", "item", changeText(c));
    item.style.whiteSpace = "pre-wrap";
    return item;
  }));

  $("goals").replaceChildren(...(goals.length ? goals.map((g) => {
    const item = el("div", "item");
    item.append(el("span", "kind", `prioridade ${g.priority} · ${GOAL_STATUS[g.status]} · origem: ${g.origin}`),
      el("div", "", g.description));
    const row = el("div", "row");
    const set = (status, label) => button(label, () =>
      api(`/api/goals/${g.id}`, { method: "PATCH", body: JSON.stringify({ status }) }));
    if (g.status !== "active") row.append(set("active", "Ativar"));
    if (g.status === "active") row.append(set("paused", "Pausar"), set("done", "Concluir"));
    if (g.status !== "cancelled" && g.status !== "done") row.append(set("cancelled", "Cancelar"));
    item.append(row);
    return item;
  }) : [el("p", "kind", "Nenhum objetivo ainda.")]));
}

$("add-kind").replaceChildren(...ADDABLE.map((k) => {
  const o = el("option", "", KIND[k]);
  o.value = k;
  return o;
}));
$("add-entry").addEventListener("submit", (ev) => {
  ev.preventDefault();
  run(async () => {
    await api("/api/identity/entries", {
      method: "POST", body: JSON.stringify({ kind: $("add-kind").value, value: $("add-value").value }),
    });
    $("add-value").value = "";
  });
});
$("add-goal").addEventListener("submit", (ev) => {
  ev.preventDefault();
  run(async () => {
    await api("/api/goals", {
      method: "POST",
      body: JSON.stringify({ description: $("goal-text").value, priority: Number($("goal-priority").value) }),
    });
    $("goal-text").value = "";
  });
});

run(async () => {});
