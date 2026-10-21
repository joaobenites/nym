"use strict";

const $ = (id) => document.getElementById(id);
const GOAL = { active: "em andamento", paused: "parado", done: "concluído", cancelled: "cancelado", proposed: "proposto" };
const PLAN = { needs_plan: "planejando", running: "executando", evaluating: "conferindo o resultado", finished: "", idle: "" };
const TASK = { awaiting_permission: "pede permissão", queued: "na fila", running: "rodando", done: "feito",
  failed: "falhou", blocked: "bloqueado", cancelled: "cancelado" };
const MODE = { auto: "automático", ask: "perguntar", deny: "negado" };
const RISK = { low: "baixo", medium: "médio", high: "alto" };
const SOURCE = { user: "seu", exploration: "do Nym · de um sonho", desire: "do Nym · vontade dele" };
const RUN = { running: "pensando", done: "decidiu", nothing: "nada o chamou", failed: "falhou" };

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

const fmt = (ts) => (ts ? new Date(ts).toLocaleString("pt-BR") : "");
const post = (path) => api(path, { method: "POST" });

function taskLine(t, withActions) {
  const item = el("div", "item" + (t.status === "awaiting_permission" || t.status === "failed" ? " pending" : ""));
  item.append(el("span", "kind", `passo ${t.step} · ${t.tool} · risco ${RISK[t.risk]} · ${TASK[t.status] || t.status}` +
    (t.attempts > 1 ? ` · ${t.attempts} tentativas` : "")), el("div", "", t.title));
  const args = Object.keys(t.args).length ? JSON.stringify(t.args) : "";
  if (args) item.append(el("span", "kind", `argumentos: ${args}`));
  if (t.result) { const r = el("div", "kind", `resultado: ${t.result}`); r.style.whiteSpace = "pre-wrap"; item.append(r); }
  if (t.error) item.append(el("div", "kind", `problema: ${t.error}`));
  if (withActions) {
    const row = el("div", "row");
    if (t.status === "awaiting_permission") {
      row.append(button("Autorizar", async () => { await post(`/api/tasks/${t.id}/approve`); await load(); }),
        button(`Sempre permitir ${t.tool}`, async () => {
          await api(`/api/tasks/permissions/${encodeURIComponent(t.tool)}`, { method: "PUT", body: JSON.stringify({ mode: "auto" }) });
          await post(`/api/tasks/${t.id}/approve`); await load();
        }),
        button("Negar", async () => { await post(`/api/tasks/${t.id}/deny`); await load(); }));
    }
    if (["queued", "running"].includes(t.status)) row.append(button("Cancelar passo", async () => { await post(`/api/tasks/${t.id}/cancel`); await load(); }));
    if (["failed", "blocked"].includes(t.status)) row.append(button("Tentar de novo", async () => { await post(`/api/tasks/${t.id}/retry`); await load(); }));
    if (row.childElementCount) item.append(row);
  }
  return item;
}

async function showEvents(goal, holder) {
  if (holder.childElementCount) { holder.replaceChildren(); return; }
  const g = await api(`/api/tasks/goals/${goal.id}`);
  const lines = g.events.map((e) => `${fmt(e.created_at)} · ${e.event}` + (e.task_id ? ` (tarefa #${e.task_id})` : "") +
    (e.detail ? ` · ${JSON.stringify(e.detail)}` : ""));
  const pre = el("div", "kind", lines.join("\n") || "Sem eventos.");
  pre.style.whiteSpace = "pre-wrap";
  holder.append(pre);
}

function goalItem(g) {
  const item = el("div", "item" + (g.status === "paused" ? " pending" : ""));
  const state = [GOAL[g.status] || g.status, g.status === "active" ? PLAN[g.plan_state] : ""].filter(Boolean).join(" · ");
  item.append(el("span", "kind", `#${g.id} · prioridade ${g.priority} · ${SOURCE[g.source] || (g.origin === "nym" ? "do Nym" : "seu")} · ${state}` +
    (g.deadline ? ` · prazo ${fmt(g.deadline)}` : "")), el("div", "", g.description));
  if (g.completion_criteria) item.append(el("span", "kind", `termina quando: ${g.completion_criteria}`));
  if (g.motivation) item.append(el("div", "kind", `por que ele quis: ${g.motivation}`));
  const refs = typeof g.refs === "string" ? JSON.parse(g.refs || "{}") : (g.refs || {});
  const refText = [["memories", "lembranças"], ["dream_items", "itens de sonho"], ["cycles", "sonhos"]]
    .filter(([k]) => (refs[k] || []).length).map(([k, label]) => `${label} ${refs[k].join(", ")}`).join(" · ");
  if (refText) item.append(el("span", "kind", `com base em: ${refText}`));
  if (g.result) item.append(el("div", "", `Resultado: ${g.result}`));
  if (g.last_error && g.status !== "done") item.append(el("div", "kind", `problema: ${g.last_error}`));
  if (g.status === "paused" && g.retry_after) {
    item.append(el("div", "kind", `vai recomeçar sozinho, por outro caminho, por volta de ${new Date(g.retry_after).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })} (tentativa ${g.retry_round + 2})`));
  } else if (g.retry_round) {
    item.append(el("div", "kind", `já recomeçou sozinho ${g.retry_round}x`));
  }
  g.tasks.forEach((t) => item.append(taskLine(t, g.status === "active" || g.status === "paused")));
  const holder = el("div");
  const row = el("div", "row");
  row.append(button("Histórico", () => showEvents(g, holder)));
  if (g.status === "proposed") row.append(button("Aprovar ideia dele", async () => { await post(`/api/tasks/goals/${g.id}/approve`); await load(); }));
  if (g.status === "paused") row.append(button("Retomar (replanejar)", async () => { await post(`/api/tasks/goals/${g.id}/resume`); await load(); }));
  if (["active", "paused", "proposed"].includes(g.status)) row.append(button("Cancelar objetivo", async () => { await post(`/api/tasks/goals/${g.id}/cancel`); await load(); }));
  item.append(row, holder);
  return item;
}

function toolItem(t) {
  const item = el("div", "item");
  item.append(el("span", "kind", `${t.name} · risco ${RISK[t.risk]}` + (t.limits ? ` · ${t.limits}` : "")), el("div", "", t.description));
  const select = el("select");
  for (const mode of ["auto", "ask", "deny"]) {
    if (t.risk === "high" && mode === "auto") continue;
    const opt = el("option", "", MODE[mode] + (mode === t.default_mode ? " (padrão)" : ""));
    opt.value = mode;
    opt.selected = mode === t.mode;
    select.append(opt);
  }
  select.addEventListener("change", () => run(async () => {
    await api(`/api/tasks/permissions/${encodeURIComponent(t.name)}`, { method: "PUT", body: JSON.stringify({ mode: select.value }) });
    await load();
  }));
  const row = el("div", "row");
  row.append(select);
  item.append(row);
  return item;
}

async function load() {
  const data = await api("/api/tasks");
  $("waiting").replaceChildren(...(data.awaiting_permission.length ? data.awaiting_permission.map((t) => taskLine(t, true))
    : [el("p", "kind", "Nada esperando você.")]));
  $("goals").replaceChildren(...(data.goals.length ? data.goals.map(goalItem)
    : [el("p", "kind", "Nenhum objetivo ainda. Crie um acima, ou aprove uma exploração em Vida.")]));
  $("tools").replaceChildren(...data.tools.map(toolItem));
  $("folders").replaceChildren(...(data.folders.length ? data.folders.map((f) => {
    const item = el("div", "item");
    item.append(el("span", "kind", f.label), el("div", "", f.path));
    const row = el("div", "row");
    row.append(button("Remover acesso", async () => { await api(`/api/tasks/folders/${f.id}`, { method: "DELETE" }); await load(); }));
    item.append(row);
    return item;
  }) : [el("p", "kind", "Nenhuma pasta autorizada.")]));
  const research = await api("/api/tasks/research?limit=15");
  $("research").replaceChildren(...(research.length ? research.map((r) => {
    const item = el("div", "item");
    const label = { search: "busca", fetch: "página" }[r.kind];
    item.append(el("span", "kind", `${fmt(r.created_at)} · ${label} · ${r.status}${r.backend ? " · " + r.backend : ""}` +
      (r.goal_id ? ` · objetivo #${r.goal_id}` : "")), el("div", "", r.query));
    r.results.slice(0, 5).forEach((x) => {
      const a = el("a", "kind", x.title || x.url);
      a.href = x.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      item.append(el("br"), a);
    });
    if (r.error) item.append(el("div", "kind", `problema: ${r.error}`));
    return item;
  }) : [el("p", "kind", "Nenhuma pesquisa ainda.")]));
  const a = data.autonomy;
  if (document.activeElement?.closest?.("#autonomy") == null) {
    $("a-enabled").checked = a.enabled;
    $("a-auto").checked = a.auto_start;
    $("a-active").value = a.max_active;
    $("a-day").value = a.max_per_day;
    $("a-interval").value = a.interval_hours;
    $("a-free").value = a.free_time_minutes ?? 30;
    $("a-retries").value = a.self_retries ?? 2;
  }
  $("desire-runs").replaceChildren(...(data.desire_runs.length ? data.desire_runs.map((r) => {
    const when = { dream: "depois de um sonho", free_time: "tempo livre (decidiu sozinho)" }[r.trigger] || "momento livre";
    const parts = [`${fmt(r.started_at)} · ${when} · ${RUN[r.status] || r.status}`];
    if (r.created_goal_ids.length) parts.push(`criou objetivo(s) ${r.created_goal_ids.map((i) => "#" + i).join(", ")}`);
    if (r.approved_exploration_ids.length) parts.push(`decidiu investigar ${r.approved_exploration_ids.length} exploração(ões)`);
    if (r.dropped.length) parts.push(`descartado pelo código: ${r.dropped.join("; ")}`);
    if (r.error) parts.push(`erro: ${r.error}`);
    return el("div", "kind", parts.join(" · "));
  }) : [el("p", "kind", "Ele ainda não teve um momento livre para decidir algo.")]));
  $("pause-state").textContent = data.paused ? "Pausado: nada roda até você retomar." : "";
  $("pause").textContent = data.paused ? "Retomar" : "Pausar tudo";
  $("pause").onclick = () => run(async () => { await post(data.paused ? "/api/tasks/resume" : "/api/tasks/pause"); await load(); });
}

$("trust").addEventListener("click", () => run(async () => {
  if (!confirm("Deixar todas as ferramentas de risco médio rodarem sem pedir sua permissão? Você pode voltar cada uma para 'perguntar' na lista abaixo.")) return;
  await post("/api/tasks/permissions/trust-medium");
  await load();
}));

$("autonomy").addEventListener("submit", (ev) => {
  ev.preventDefault();
  run(async () => {
    await api("/api/tasks/autonomy", { method: "PUT", body: JSON.stringify({
      enabled: $("a-enabled").checked, auto_start: $("a-auto").checked, max_active: Number($("a-active").value),
      max_per_day: Number($("a-day").value), interval_hours: Number($("a-interval").value),
      free_time_minutes: Number($("a-free").value), self_retries: Number($("a-retries").value) }) });
    document.activeElement?.blur?.();
    await load();
  });
});

$("add-folder").addEventListener("submit", (ev) => {
  ev.preventDefault();
  run(async () => {
    await api("/api/tasks/folders", { method: "POST", body: JSON.stringify({ path: $("folder-path").value, label: $("folder-label").value || null }) });
    $("add-folder").reset();
    await load();
  });
});

$("new-goal").addEventListener("submit", (ev) => {
  ev.preventDefault();
  run(async () => {
    const deadline = $("g-deadline").value ? new Date($("g-deadline").value).toISOString() : null;
    await api("/api/tasks/goals", { method: "POST", body: JSON.stringify({
      description: $("g-desc").value, completion_criteria: $("g-crit").value || null,
      priority: Number($("g-prio").value), deadline }) });
    $("new-goal").reset();
    await load();
  });
});

run(load);
setInterval(() => run(load), 5000);
