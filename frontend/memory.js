"use strict";

const $ = (id) => document.getElementById(id);
const KIND = { episodic: "episódio", semantic: "fato", identity: "observação" };
const EPI = { confirmed: "confirmado", sourced: "de fonte externa", hypothesis: "hipótese", unverified: "não verificado" };
const ORIGIN = { user_stated: "dito por você", nym_inferred: "deduzido pelo Nym", external_source: "fonte externa", user_edit: "editado por você", system: "sistema" };
const STATUS = { active: "ativa", archived: "arquivada", superseded: "substituída" };
const JOB = { pending: "pendente", done: "concluído", failed: "falhou", skipped: "ignorado" };

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  let data = null;
  try { data = await res.json(); } catch { /* sem corpo */ }
  if (!res.ok) throw new Error(Array.isArray(data?.detail) ? "Valor inválido." : (data?.detail || `Erro HTTP ${res.status}`));
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
}

function fmt(ts) { return ts ? new Date(ts).toLocaleString("pt-BR") : ""; }

async function showDetails(m, holder) {
  if (holder.childElementCount) { holder.replaceChildren(); return; }
  const d = await api(`/api/memories/${m.id}`);
  const lines = [];
  if (d.outcome) lines.push(`Resultado: ${d.outcome}`);
  lines.push(`Origem: ${ORIGIN[d.origin] || d.origin} · importância ${d.importance} · confiança ${d.confidence} · repetida ${d.recurrence}x`);
  lines.push("Fontes:");
  d.sources.forEach((s) => {
    if (s.source_type === "manual") lines.push("  • criada manualmente");
    else if (s.message) lines.push(`  • ${s.message.role === "user" ? "você" : "Nym"} (${fmt(s.message.created_at)}): ${s.message.content.slice(0, 200)}`);
    else lines.push(`  • mensagem #${s.message_id} (já não existe)`);
  });
  if (d.links.length) {
    lines.push("Ligações:");
    d.links.forEach((l) => lines.push(`  • #${l.from_id} ${l.relation} #${l.to_id}`));
  }
  lines.push("Histórico:");
  d.history.forEach((h) => lines.push(`  • ${fmt(h.created_at)} · ${h.actor} · ${h.action}` +
    (h.old_value ? ` · antes: ${h.old_value}` : "") + (h.new_value && h.action !== "created" ? ` · depois: ${h.new_value}` : "") +
    (h.reason ? ` · ${h.reason}` : "")));
  const pre = el("div", "kind", lines.join("\n"));
  pre.style.whiteSpace = "pre-wrap";
  holder.append(pre);
}

function memoryItem(m, reload) {
  const item = el("div", "item");
  const tag = [KIND[m.kind], m.epistemic ? EPI[m.epistemic] : null, STATUS[m.status], fmt(m.occurred_at)].filter(Boolean).join(" · ");
  item.append(el("span", "kind", `#${m.id} · ${tag}`), el("div", "", m.content));
  const holder = el("div");
  const row = el("div", "row");
  row.append(button("Detalhes", () => showDetails(m, holder)));
  if (m.status === "active") {
    row.append(button("Editar", async () => {
      const content = window.prompt("Novo texto:", m.content);
      if (content === null) return;
      await api(`/api/memories/${m.id}`, { method: "PATCH", body: JSON.stringify({ content }) });
      await reload();
    }));
    if (m.kind !== "episodic") {
      ["confirmed", "hypothesis", "unverified"].filter((e) => e !== m.epistemic).forEach((e) =>
        row.append(button(`Marcar como ${EPI[e]}`, async () => {
          await api(`/api/memories/${m.id}`, { method: "PATCH", body: JSON.stringify({ epistemic: e }) });
          await reload();
        })));
    }
    row.append(button("Arquivar", async () => { await api(`/api/memories/${m.id}/archive`, { method: "POST" }); await reload(); }));
  } else {
    row.append(button("Restaurar", async () => { await api(`/api/memories/${m.id}/restore`, { method: "POST" }); await reload(); }));
  }
  row.append(button("Apagar", async () => {
    if (!window.confirm("Apagar de vez esta lembrança, com suas fontes e histórico?")) return;
    const purge = window.confirm("Apagar TAMBÉM as mensagens do chat que deram origem a ela (e tudo o que foi extraído dessas mensagens, inclusive o resumo do episódio)?\n\nOK = apagar tudo · Cancelar = apagar só esta lembrança");
    await api(`/api/memories/${m.id}?purge_sources=${purge}`, { method: "DELETE" });
    await reload();
  }));
  item.append(row, holder);
  return item;
}

async function loadList() {
  const p = new URLSearchParams();
  if ($("f-kind").value) p.set("kind", $("f-kind").value);
  if ($("f-epistemic").value) p.set("epistemic", $("f-epistemic").value);
  p.set("status", $("f-status").value);
  if ($("f-q").value.trim()) p.set("q", $("f-q").value.trim());
  const items = await api(`/api/memories?${p}`);
  $("list").replaceChildren(...(items.length ? items.map((m) => memoryItem(m, loadList))
    : [el("p", "kind", "Nada por aqui ainda. As lembranças aparecem depois que o Nym consolida uma conversa.")]));
}

async function loadJobs() {
  const jobs = await api("/api/memories/jobs?limit=8");
  $("jobs").replaceChildren(...jobs.map((j) => {
    const item = el("div", "item" + (j.status === "failed" ? " pending" : ""));
    item.append(el("span", "kind",
      `trecho #${j.start_message_id}–#${j.end_message_id} · ${j.message_count} mensagens · ${JOB[j.status]}` +
      (j.attempts ? ` · ${j.attempts} tentativa(s)` : "") + (j.last_error ? ` · ${j.last_error}` : "")));
    return item;
  }));
}

function probeItem(h) {
  const item = el("div", "item");
  const c = h.components;
  item.append(el("span", "kind", `#${h.memory.id} · nota ${h.score} · ${h.why}` + (h.conflicted ? " · em conflito" : "")),
    el("div", "", h.memory.content),
    el("span", "kind", Object.entries(c).map(([k, v]) => `${k} ${v}`).join(" · ")));
  return item;
}

$("filters").addEventListener("submit", (ev) => { ev.preventDefault(); run(loadList); });
$("add").addEventListener("submit", (ev) => {
  ev.preventDefault();
  run(async () => {
    await api("/api/memories", { method: "POST", body: JSON.stringify({ content: $("add-content").value }) });
    $("add-content").value = "";
    await loadList();
  });
});
$("probe").addEventListener("submit", (ev) => {
  ev.preventDefault();
  run(async () => {
    const hits = await api(`/api/memories/retrieve?q=${encodeURIComponent($("probe-q").value)}`);
    $("probe-result").replaceChildren(...(hits.length ? hits.map(probeItem) : [el("p", "kind", "Nenhuma lembrança relevante para essa frase.")]));
  });
});
$("consolidate").addEventListener("click", () => run(async () => {
  $("consolidate").disabled = true;
  $("consolidate-result").textContent = "Consolidando… pode levar alguns segundos por trecho.";
  try {
    const r = await api("/api/memories/consolidate", { method: "POST", body: JSON.stringify({ force: true }) });
    $("consolidate-result").textContent = r.status === "busy" ? "Já existe uma consolidação em andamento."
      : `${r.planned} trecho(s) novo(s); ${r.processed.length} processado(s); ${r.pending} pendente(s).`;
  } finally { $("consolidate").disabled = false; }
  await Promise.all([loadList(), loadJobs()]);
}));

$("error").textContent = "";
run(async () => { await Promise.all([loadList(), loadJobs()]); });
