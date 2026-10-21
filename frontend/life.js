"use strict";

const $ = (id) => document.getElementById(id);
const STATUS = { running: "em andamento", completed: "concluído", partial: "parcial", interrupted: "interrompido", failed: "falhou" };
const TRIGGER = { manual: "manual", idle: "ocioso", scheduled: "agendado" };
const KIND = { association: "ligação", hypothesis: "hipótese", open_question: "pergunta", dream_note: "assunto para conversar",
  consolidation: "consolidação", archive: "arquivadas", identity_proposal: "proposta de identidade" };
const STAGE = { selecao: "seleção", revisao: "revisão", associacao: "associação", consolidacao: "consolidação",
  exploracao: "exploração", reflexao_identidade: "reflexão sobre identidade", registro: "registro" };

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

async function loadState() {
  const st = await api("/api/state");
  $("state-note").textContent = `${st.note} Agora: ${st.label}.`;
  const rows = [["Energia", st.energy], ["Curiosidade", st.curiosity], ["Vínculo", st.bond]].map(([name, v]) => {
    const item = el("div", "item");
    const bar = el("div", "bar");
    const fill = el("span");
    fill.style.width = `${Math.round(v * 100)}%`;
    bar.append(fill);
    item.append(el("span", "kind", `${name}: ${Math.round(v * 100)}%`), bar);
    return item;
  });
  const why = el("p", "kind", st.reasons.length ? `Por quê: ${st.reasons.join("; ")}.` : "Sem fatores em destaque no momento.");
  $("state").replaceChildren(...rows, why);
}

const TASTE_SRC = { pesquisa: "pesquisas", criacao: "criações", objetivo: "objetivos", sonho: "sonhos", conversa: "conversas",
  reacao: "reações", frustracao: "tentativas que falharam" };

async function loadTastes() {
  const data = await api("/api/tastes");
  $("taste-note").textContent = data.note;
  if (!data.tastes.length) {
    $("tastes").replaceChildren(el("p", "kind", "Ainda sem gostos formados: precisam de registros de mais de um tipo, em mais de um dia."));
    return;
  }
  const nodes = data.tastes.map((t) => {
    const item = el("div", "item");
    const sign = t.valence > 0 ? "curte" : "não curte";
    const state = { active: "", faded: " (esmaeceu)", vetoed: " (vetado por você)" }[t.status] || "";
    item.append(el("strong", "", `${t.label}${state}`), el("span", "kind", ` · ${sign} · ${t.origin === "own" ? "por conta própria" : "compartilhado com você"}`));
    const bar = el("div", "bar");
    const fill = el("span");
    fill.style.width = `${Math.round(t.strength * 100)}%`;
    bar.append(fill);
    const srcs = Object.entries(t.sources).map(([k, n]) => `${n} ${TASTE_SRC[k] || k}`).join(", ");
    item.append(bar, el("p", "kind", `${t.items} registros em ${t.days} dia(s): ${srcs}.`));
    const ev = el("details");
    ev.append(el("summary", "kind", "Evidências"));
    for (const e of t.evidence) ev.append(el("p", "kind", `${e.day} · ${TASTE_SRC[e.source] || e.source} #${e.ref}: ${e.snippet}`));
    item.append(ev);
    if (t.status === "vetoed") item.append(button("Desfazer veto", async () => { await api(`/api/tastes/${t.id}/restore`, { method: "POST" }); await loadTastes(); }));
    else item.append(button("Não é dele (vetar)", async () => { await api(`/api/tastes/${t.id}/veto`, { method: "POST" }); await loadTastes(); }));
    return item;
  });
  $("tastes").replaceChildren(...nodes);
}

async function loadPresence() {
  const p = await api("/api/presence/settings");
  $("p-enabled").checked = p.enabled;
  $("p-qs").value = p.quiet_start;
  $("p-qe").value = p.quiet_end;
  $("p-max").value = p.max_per_day;
  $("p-gap").value = p.min_gap_minutes;
  $("p-wb").value = p.welcome_back_hours;
}

async function showCycle(c, holder) {
  if (holder.childElementCount) { holder.replaceChildren(); return; }
  const d = await api(`/api/dreams/${c.id}`);
  const lines = [];
  if (d.review) lines.push(`Revisão: ${d.review}`);
  if (d.error) lines.push(`Erro: ${d.error}`);
  lines.push(`Memórias revisadas: ${d.selected_ids.length} · chamadas ao modelo: ${d.model_calls} · duração: ${d.duration_ms ?? "?"} ms`);
  lines.push("Etapas:");
  d.stage_log.forEach((s) => lines.push(`  • ${STAGE[s.stage] || s.stage}: ${s.status}${s.detail ? ` (${s.detail})` : ""}`));
  if (d.items.length) lines.push("Resultados:");
  d.items.forEach((i) => lines.push(`  • ${KIND[i.kind] || i.kind}: ${i.content}` + (i.refs.length ? `  [memórias ${i.refs.join(", ")}]` : "")));
  const pre = el("div", "kind", lines.join("\n"));
  pre.style.whiteSpace = "pre-wrap";
  holder.append(pre);
}

async function loadDreams() {
  const data = await api("/api/dreams?limit=15");
  const st = data.status;
  $("dream-status").textContent = (st.paused ? "Pausado. " : "") + (st.running ? "Sonhando agora. " : "") +
    (st.would_dream_now ? "Vai sonhar em breve. " : `Não vai sonhar agora: ${st.reason}. `) +
    `Limites: ${st.limits.max_per_day}/dia, ${st.limits.max_seconds}s por ciclo, ${st.limits.max_memories} memórias.`;
  $("dream-pause").textContent = st.paused ? "Retomar sonhos" : "Pausar sonhos";
  $("dream-pause").onclick = () => run(async () => {
    await api(st.paused ? "/api/dreams/resume" : "/api/dreams/pause", { method: "POST" });
    await loadDreams();
  });
  $("cycles").replaceChildren(...(data.cycles.length ? data.cycles.map((c) => {
    const item = el("div", "item" + (c.status === "failed" ? " pending" : ""));
    item.append(el("span", "kind", `#${c.id} · ${fmt(c.started_at)} · ${TRIGGER[c.trigger]} · ${STATUS[c.status]}`),
      el("div", "", c.review || (c.error ? `(${c.error})` : "(sem resumo)")));
    const holder = el("div");
    const row = el("div", "row");
    row.append(button("Detalhes", () => showCycle(c, holder)));
    item.append(row, holder);
    return item;
  }) : [el("p", "kind", "Nenhum sonho ainda. Eles acontecem quando o chat fica parado, ou quando você pedir.")]));
}

async function loadQuestions() {
  const qs = await api("/api/dreams/questions");
  $("questions").replaceChildren(...(qs.length ? qs.map((q) => {
    const item = el("div", "item");
    item.append(el("div", "", q.content), el("span", "kind", `[memórias ${q.refs.join(", ")}]`));
    const row = el("div", "row");
    row.append(button("Já respondida / dispensar", async () => { await api(`/api/dreams/questions/${q.id}/close`, { method: "POST" }); await loadQuestions(); await loadState(); }));
    item.append(row);
    return item;
  }) : [el("p", "kind", "Nenhuma pergunta em aberto.")]));
}

async function loadExploration() {
  const tasks = (await api("/api/dreams/exploration")).filter((t) => t.status === "pending" || t.status === "approved");
  $("exploration").replaceChildren(...(tasks.length ? tasks.map((t) => {
    const item = el("div", "item");
    item.append(el("span", "kind", t.status === "approved" ? "aprovada (virou objetivo em Tarefas)" : "pendente"), el("div", "", t.question),
      el("span", "kind", t.rationale || ""));
    const row = el("div", "row");
    if (t.status === "pending") row.append(button("Aprovar", async () => { await api(`/api/dreams/exploration/${t.id}/approve`, { method: "POST" }); await loadExploration(); }));
    row.append(button("Dispensar", async () => { await api(`/api/dreams/exploration/${t.id}/dismiss`, { method: "POST" }); await loadExploration(); await loadState(); }));
    item.append(row);
    return item;
  }) : [el("p", "kind", "Nenhuma tarefa de exploração.")]));
}

$("presence").addEventListener("submit", (ev) => {
  ev.preventDefault();
  run(async () => {
    await api("/api/presence/settings", { method: "PUT", body: JSON.stringify({
      enabled: $("p-enabled").checked, quiet_start: $("p-qs").value, quiet_end: $("p-qe").value,
      max_per_day: Number($("p-max").value), min_gap_minutes: Number($("p-gap").value),
      welcome_back_hours: Number($("p-wb").value) }) });
    await loadPresence();
    $("error").textContent = "";
  });
});

$("dream-run").addEventListener("click", () => run(async () => {
  $("dream-run").disabled = true;
  $("dream-result").textContent = "Sonhando… pode levar alguns segundos.";
  try {
    const r = await api("/api/dreams/run", { method: "POST" });
    $("dream-result").textContent = r.status === "completed" ? "Sonho concluído."
      : r.status === "skipped" ? `Nada a fazer: ${r.reason}.` : r.status === "busy" ? "Já há um sonho em andamento."
      : `Terminou como "${STATUS[r.status] || r.status}"${r.error ? `: ${r.error}` : ""}.`;
  } finally { $("dream-run").disabled = false; }
  await Promise.all([loadDreams(), loadQuestions(), loadExploration(), loadState()]);
}));
$("dream-cancel").addEventListener("click", () => run(async () => {
  const r = await api("/api/dreams/cancel", { method: "POST" });
  $("dream-result").textContent = r.cancelled ? "Cancelamento pedido (vale quando o modelo responder)." : "Não há sonho em andamento.";
}));

// --- sentidos (Fase 8) ---------------------------------------------------------------------------------------------
const PERCEPT = { camera: "câmera", screen: "tela", image: "imagem", file: "imagem de pasta", video: "vídeo" };
async function loadSenses() {
  const s = await api("/api/senses");
  const row = (name, ok, text) => {
    const item = el("div", "item");
    item.append(el("b", "", `${ok ? "●" : "○"} ${name}`), el("div", "kind", text));
    return item;
  };
  $("senses").replaceChildren(
    row("Visão", s.vision.available, `${s.vision.model}: ${s.vision.detail}`),
    row("Câmera e tela", s.camera.available, s.camera.detail),
    row("Assistir vídeos (YouTube)", true, `ouve: ${s.video.hearing}; vê: ${s.video.frames}`),
  );
  const data = await api("/api/perceptions?limit=50");
  $("perceptions").replaceChildren(...data.perceptions.map((p) => {
    const item = el("div", "item");
    const head = p.kind === "video" ? `${p.title || p.source}` : `${PERCEPT[p.kind] || p.kind}${p.source && p.kind === "file" ? ` · ${p.source}` : ""}`;
    item.append(el("b", "", head), el("div", "kind", `${PERCEPT[p.kind] || p.kind} · ${fmt(p.created_at)}`));
    const body = el("p", "", p.description);
    body.style.whiteSpace = "pre-wrap";
    item.append(body);
    if (p.kind === "video") {
      const a = el("a", "", p.source);
      a.href = p.source;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      item.append(a);
    }
    const r = el("div", "row");
    r.append(button("Esquecer", async () => {
      if (!confirm("Apagar este registro? Ele deixa de lembrar do que viu aqui.")) return;
      await api(`/api/perceptions/${p.id}`, { method: "DELETE" });
      await loadSenses();
    }));
    item.append(r);
    return item;
  }));
  if (!data.perceptions.length) $("perceptions").append(el("p", "kind", "Ele ainda não viu nem assistiu nada."));
}

// --- vida contínua (Fase 9) ----------------------------------------------------------------------------------------
async function loadToday() {
  const t = await api("/api/life/today");
  const list = el("ul", "day-list");
  for (const line of t.lines) list.append(el("li", "", line));
  $("today").replaceChildren(t.empty ? el("p", "kind", "Nada registrado hoje ainda.") : list);
}

async function loadDiary() {
  const d = await api("/api/diary?limit=14");
  $("diary").replaceChildren(...(d.pages.length ? d.pages.map((p) => {
    const item = el("article", "item diary-page");
    const date = new Date(`${p.day}T12:00:00`).toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long" });
    item.append(el("b", "", date));
    const body = el("p", "", p.text);
    body.style.whiteSpace = "pre-wrap";
    item.append(body);
    if (p.tomorrow) item.append(el("div", "kind", `Amanhã: ${p.tomorrow}`));
    return item;
  }) : [el("p", "kind", "Ainda sem páginas: a primeira sai no dia seguinte a um dia em que algo aconteceu.")]));
}

const PSTATUS = { active: "em andamento", done: "terminado", dropped: "largado" };
const GSTATUS = { done: "feito", paused: "travou", active: "em andamento", cancelled: "cancelado", proposed: "proposto" };
async function loadProjects() {
  const d = await api("/api/projects");
  $("projects").replaceChildren(...(d.projects.length ? d.projects.map((p) => {
    const item = el("div", "item");
    item.append(el("b", "", p.title), el("div", "kind", `${PSTATUS[p.status]} · desde ${fmt(p.created_at)}`), el("p", "", `Por quê: ${p.why}`));
    if (p.closing_note) item.append(el("div", "kind", `fechado: ${p.closing_note}`));
    const list = el("ul", "day-list");
    for (const g of p.goals) list.append(el("li", "", `${g.description} (${GSTATUS[g.status] || g.status})`));
    if (p.goals.length) item.append(list);
    if (p.status === "active") {
      const r = el("div", "row");
      r.append(button("Largar projeto", async () => { await api(`/api/projects/${p.id}/drop`, { method: "POST" }); await loadProjects(); }));
      item.append(r);
    }
    return item;
  }) : [el("p", "kind", "Nenhum projeto ainda. Ele abre um quando algo vale vários dias.")]));
}

run(async () => { await Promise.all([loadState(), loadToday(), loadDiary(), loadProjects(), loadTastes(), loadSenses(), loadPresence(), loadDreams(), loadQuestions(), loadExploration()]); });
