"use strict";
// Conversa: texto aparece enquanto é gerado (streaming), "Parar" interrompe de verdade, e o painel "Agora" mostra
// o estado, o que o Nym está fazendo, pedidos de permissão (resolvidos aqui mesmo) e um atalho para pedir tarefas.

const $ = (id) => document.getElementById(id);
const els = { log: $("log"), input: $("input"), form: $("composer"), send: $("send"), stop: $("stop") };
const state = { controller: null, oldestId: null, hasMore: false, streaming: false };

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  let data = null;
  try { data = await res.json(); } catch { /* sem corpo */ }
  if (!res.ok) {
    const err = new Error(Array.isArray(data?.detail) ? "Valor inválido." : (data?.detail || `Erro HTTP ${res.status}`));
    err.data = data;
    throw err;
  }
  return data;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;   // textContent: nunca interpreta HTML do modelo
  return node;
}

function messageNode(kind, text, meta, extra = "") {
  const div = el("div", `msg ${kind} ${extra}`.trim(), text);
  if (meta) div.append(el("span", "meta", meta));
  return div;
}

function nearBottom() { return els.log.scrollHeight - els.log.scrollTop - els.log.clientHeight < 120; }
function toBottom() { els.log.scrollTop = els.log.scrollHeight; }

function addMessage(kind, text, meta, extra) {
  els.log.querySelector(".empty")?.remove();
  const stick = nearBottom();
  const node = messageNode(kind, text, meta, extra);
  els.log.append(node);
  if (stick || kind === "user") toBottom();
  return node;
}

function historyNode(m) {
  const node = messageNode(m.role, m.content, m.initiative ? "ele puxou assunto" : null, m.initiative ? "initiative" : "");
  if (m.role === "assistant") addReactions(node, m.id, m.reaction);
  if (m.perceptions?.length) addSeen(node, m.perceptions);
  return node;
}

// Sentidos (Fase 8): o que ele viu junto da sua mensagem (descrição registrada pelo modelo de visão).
const SEEN_LABEL = { camera: "pela câmera", screen: "na sua tela", image: "na imagem", file: "na imagem" };
function addSeen(node, perceptions) {
  for (const p of perceptions) {
    const d = el("details", "seen");
    d.append(el("summary", "", `ele viu ${SEEN_LABEL[p.kind] || ""}`.trim()), el("p", "", p.description));
    node.append(d);
  }
}
window.nymNotice = (text) => addMessage("note", text);

// Reações: viram aprendizado de verdade (lições de conversa com placar; ver Ateliê → Aprendizados).
function addReactions(node, id, value) {
  if (!id) return;
  const box = el("span", "react");
  const make = (v, label, title) => {
    const b = el("button", value === v ? "on" : "", label);
    b.type = "button";
    b.title = title;
    b.setAttribute("aria-pressed", String(value === v));
    b.addEventListener("click", async () => {
      const next = value === v ? 0 : v;
      try {
        await api("/api/feedback", { method: "POST", body: JSON.stringify({ target: "message", id, value: next }) });
        value = next || null;
        box.querySelectorAll("button").forEach((x, i) => {
          const on = value === (i === 0 ? 1 : -1);
          x.classList.toggle("on", on);
          x.setAttribute("aria-pressed", String(on));
        });
      } catch (err) { addMessage("error", err.message); }
    });
    return b;
  };
  box.append(make(1, "👍", "Gostei desta resposta (ele aprende com isso)"), make(-1, "👎", "Não gostei (ele aprende com isso)"));
  node.append(box);
  node.addEventListener("click", (ev) => {   // toque: mostra as reações desta mensagem (no mouse aparecem ao passar)
    if (ev.target.closest("button, a, details")) return;
    els.log.querySelectorAll(".msg.tapped").forEach((m) => { if (m !== node) m.classList.remove("tapped"); });
    node.classList.toggle("tapped");
  });
}

// Separadores de dia ("Hoje", "Ontem", "12 de outubro"): o tempo entre as conversas fica visível, como pro Nym.
function dayLabel(iso) {
  const d = new Date(iso);
  const today = new Date();
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(today) - start(d)) / 86400000);
  if (diff === 0) return "Hoje";
  if (diff === 1) return "Ontem";
  return d.toLocaleDateString("pt-BR", { day: "numeric", month: "long", ...(d.getFullYear() !== today.getFullYear() ? { year: "numeric" } : {}) });
}
function withDays(messages) {
  const out = [];
  let prev = null;
  for (const m of messages) {
    const day = m.created_at ? new Date(m.created_at).toDateString() : null;
    if (day && day !== prev) out.push(el("div", "day", dayLabel(m.created_at)));
    prev = day || prev;
    const node = historyNode(m);
    if (m.created_at) node.title = new Date(m.created_at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
    out.push(node);
  }
  return out;
}
function ensureToday() {
  const today = new Date().toDateString();
  const days = els.log.querySelectorAll(".day");
  const last = days[days.length - 1];
  if (!last || (last.dataset.day !== today && last.textContent !== "Hoje")) {
    const d = el("div", "day", "Hoje");
    d.dataset.day = today;
    els.log.append(d);
  }
}

function olderButton() {
  els.log.querySelector(".older")?.remove();
  if (!state.hasMore) return;
  const b = el("button", "older", "Mensagens anteriores");
  b.type = "button";
  b.addEventListener("click", loadOlder);
  els.log.prepend(b);
}

async function loadHistory() {
  try {
    const data = await api("/api/chat/history?limit=100");
    els.log.replaceChildren();
    if (!data.messages.length) els.log.append(el("p", "empty", "Nym está aqui. Diga oi quando quiser."));
    els.log.append(...withDays(data.messages));
    state.hasMore = data.has_more;
    if (data.messages.length) state.oldestId = data.messages[0].id;
    olderButton();
    toBottom();
  } catch (err) {
    addMessage("error", `Não foi possível carregar o histórico: ${err.message}`);
  }
}

async function loadOlder() {
  try {
    const data = await api(`/api/chat/history?limit=100&before_id=${state.oldestId}`);
    const previousHeight = els.log.scrollHeight;
    els.log.querySelector(".older")?.remove();
    els.log.querySelector(".day")?.remove();          // o primeiro separador volta, se for o caso, no lote de cima
    els.log.prepend(...withDays(data.messages));
    state.hasMore = data.has_more;
    if (data.messages.length) state.oldestId = data.messages[0].id;
    olderButton();
    els.log.scrollTop = els.log.scrollHeight - previousHeight;
  } catch (err) {
    addMessage("error", `Não foi possível carregar mensagens anteriores: ${err.message}`);
  }
}

function setStreaming(on) {
  state.streaming = on;
  els.send.hidden = on;
  els.stop.hidden = !on;
  els.input.disabled = on;
  window.nymShell?.setBusy(on);
  if (!on) els.input.focus();
}

function timingText(t, used) {
  const parts = [];
  if (t?.server_s !== undefined) parts.push(`${t.server_s.toFixed(1).replace(".", ",")} s`);
  if (t?.tokens_per_s) parts.push(`${Math.round(t.tokens_per_s)} tok/s`);
  if (t?.load_s > 1) parts.push(`carregou o modelo em ${t.load_s.toFixed(1).replace(".", ",")} s`);
  if (used?.length) parts.push(`lembrou de ${used.length}`);
  return parts.join(" · ");
}

async function send(text, images = []) {
  ensureToday();
  const userNode = addMessage("user", text);
  if (images.length) {
    const strip = el("div", "shots");
    images.forEach((i) => { const img = el("img"); img.src = i.data; img.alt = i.name; strip.append(img); });
    userNode.append(strip);
  }
  const bubble = addMessage("assistant", "", null, "streaming");
  let content = "";
  state.controller = new AbortController();
  setStreaming(true);
  setPetMode("thinking");
  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, images }), signal: state.controller.signal,
    });
    if (!res.ok) {
      let detail = `Erro HTTP ${res.status}`;
      try { detail = (await res.json()).detail || detail; } catch { /* sem corpo */ }
      throw new Error(Array.isArray(detail) ? "Mensagem inválida." : detail);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.type === "seeing") {
          bubble.textContent = images.length > 1 ? "olhando as imagens…" : "olhando…";
        } else if (ev.type === "seen") {
          bubble.textContent = "";
          addSeen(userNode, ev.perceptions);
        } else if (ev.type === "delta") {
          const stick = nearBottom();
          if (!content) setPetMode("talking");
          content += ev.text;
          bubble.textContent = content;
          if (stick) toBottom();
        } else if (ev.type === "done" || ev.type === "stopped") {
          bubble.textContent = ev.reply;
          bubble.append(el("span", "meta", (ev.type === "stopped" ? "interrompida · " : "") + timingText(ev.timing, ev.memories_used)));
          addReactions(bubble, ev.assistant_message_id, null);
          (ev.actions || []).forEach((a) => addMessage("note", `Começou a fazer: ${a.description}. Acompanhe em "Fazendo".`));
        } else if (ev.type === "correction") {
          // Fase 10: ele percebeu que falou sem checar e se corrigiu em seguida (mensagem separada, já salva)
          const fix = addMessage("assistant", ev.text);
          addReactions(fix, ev.assistant_message_id, null);
        } else if (ev.type === "error") {
          if (!content) bubble.remove();
          addMessage("error", ev.detail + (ev.retryable ? " Sua mensagem foi salva; tente de novo quando o modelo voltar." : ""));
        }
      }
    }
    if (!content && bubble.isConnected && !bubble.textContent) bubble.remove();
  } catch (err) {
    if (err.name === "AbortError") {
      if (!content) bubble.remove();
      else bubble.append(el("span", "meta", "interrompida"));
    } else {
      if (!content) bubble.remove();
      addMessage("error", err.message);
    }
  } finally {
    bubble.classList.remove("streaming");
    state.controller = null;
    setStreaming(false);
    setPetMode(null);
    refreshPanel();
  }
}

// --- painel "Agora" ------------------------------------------------------------------------------------------
function meter(name, v) {
  const row = el("div", "meter");
  const bar = el("div", "bar");
  const fill = el("span");
  fill.style.width = `${Math.round(v * 100)}%`;
  bar.append(fill);
  row.append(el("span", "", name), bar, el("span", "", `${Math.round(v * 100)}%`));
  return row;
}

async function loadMood() {
  const st = await api("/api/state");
  pet.mood = st.label;
  const head = el("div", "mood");
  head.append(el("b", "", st.label), el("span", "kind", "calculado, não sentido"));
  const why = el("div", "kind", st.reasons.length ? st.reasons.join(" · ") : "");
  $("mood").replaceChildren(head, meter("Energia", st.energy), meter("Curiosidade", st.curiosity), meter("Vínculo", st.bond), why);
}

function stepsBar(tasks) {
  const box = el("div", "steps");
  tasks.forEach((t) => {
    const i = el("i", { done: "done", awaiting_permission: "wait", failed: "fail", blocked: "fail" }[t.status] || "");
    i.title = `${t.title} (${t.status})`;
    box.append(i);
  });
  return box;
}

async function decide(task, mode) {
  if (mode === "always") await api(`/api/tasks/permissions/${encodeURIComponent(task.tool)}`, { method: "PUT", body: JSON.stringify({ mode: "auto" }) });
  await api(`/api/tasks/${task.id}/${mode === "deny" ? "deny" : "approve"}`, { method: "POST" });
  await refreshPanel();
  window.nymShell?.refresh();
}

function button(label, cls, handler) {
  const b = el("button", cls, label);
  b.type = "button";
  b.addEventListener("click", async () => {
    b.disabled = true;
    try { await handler(); } catch (err) { addMessage("error", err.message); } finally { b.disabled = false; }
  });
  return b;
}

async function loadTasks() {
  const data = await api("/api/tasks");
  pet.waiting = data.awaiting_permission.length + data.goals.filter((g) => g.status === "proposed").length;
  pet.working = data.goals.filter((g) => g.status === "active").length;
  const perms = data.awaiting_permission.map((t) => {
    const card = el("div", "card alert");
    card.append(el("h2", "", "Pede permissão"), el("div", "line", t.title),
      el("div", "kind", `${t.tool}${Object.keys(t.args).length ? " · " + JSON.stringify(t.args) : ""}`));
    const actions = el("div", "actions");
    actions.append(button("Permitir", "primary", () => decide(t, "once")),
      button(`Sempre permitir ${t.tool}`, "", () => decide(t, "always")),
      button("Negar", "danger", () => decide(t, "deny")));
    card.append(actions);
    return card;
  });
  const proposed = data.goals.filter((g) => g.status === "proposed").map((g) => {
    const card = el("div", "card alert");
    card.append(el("h2", "", "Ideia dele"), el("div", "line", g.description));
    if (g.motivation) card.append(el("div", "kind", g.motivation));
    const actions = el("div", "actions");
    actions.append(button("Pode fazer", "primary", async () => { await api(`/api/tasks/goals/${g.id}/approve`, { method: "POST" }); await refreshPanel(); }),
      button("Agora não", "", async () => { await api(`/api/tasks/goals/${g.id}/cancel`, { method: "POST" }); await refreshPanel(); }));
    card.append(actions);
    return card;
  });
  $("permissions").replaceChildren(...perms, ...proposed);
  const active = data.goals.filter((g) => g.status === "active");
  const recent = data.goals.filter((g) => g.status === "done" || g.status === "paused").slice(0, 2);
  const rows = [...active, ...recent].map((g) => {
    const box = el("div", "line");
    const who = g.origin === "nym" ? "ideia dele · " : "";
    const label = { active: g.plan_state === "evaluating" ? "conferindo" : g.plan_state === "needs_plan" ? "planejando" : "fazendo",
      done: "feito", paused: g.retry_after ? "travou; vai tentar de novo sozinho" : "parou" }[g.status];
    box.append(el("div", "", g.description), el("div", "kind", `${who}${label}`));
    if (g.tasks.length) box.append(stepsBar(g.tasks));
    if (g.status === "done" && g.result) box.append(el("div", "kind", g.result));
    return box;
  });
  $("doing").replaceChildren(...(rows.length ? rows : [el("div", "kind", "Nada agora. Peça algo abaixo, ou ele inventa algo depois de sonhar.")]));
}

async function loadDream() {
  const data = await api("/api/dreams?limit=1");
  const c = data.cycles.find((x) => x.status === "completed" || x.status === "partial");
  pet.dreaming = Boolean(data.status.running);
  $("dream").replaceChildren(c ? el("div", "line", c.review || "(sem resumo)") : el("div", "kind", "Ainda não sonhou."),
    el("div", "kind", data.status.running ? "sonhando agora…" : c ? new Date(c.started_at).toLocaleString("pt-BR") : ""));
}

async function refreshPanel() {
  for (const fn of [loadGrowth, loadMood, loadTasks, loadDream]) {
    try { await fn(); } catch { /* painel é auxiliar */ }
  }
  setPetMode(null);
}

// --- avatar: forma = estágio real, modo = estado real ------------------------------------------------------------
const pet = { view: window.NymAvatar?.create($("pet")), mini: window.NymAvatar?.create($("mini-pet")), waiting: 0,
  working: 0, dreaming: false, chat: null, mood: "" };

/** `chatMode` vem do streaming ("thinking"/"talking"); null = decidir pelo estado do backend. */
function setPetMode(chatMode) {
  if (!pet.view) return;
  if (chatMode !== undefined) pet.chat = chatMode;
  const online = document.body.dataset.online !== "0";
  const mode = !online ? "off"
    : state.streaming && pet.chat ? pet.chat
    : pet.waiting ? "waiting"
    : pet.dreaming ? "dreaming"
    : pet.working ? "working"
    : "idle";
  pet.view.setMode(mode);
  pet.mini?.setMode(mode);
  $("pet-mode").textContent = pet.view.label(mode);
  const doing = { thinking: "pensando…", talking: "escrevendo…", dreaming: "sonhando", working: "trabalhando numa coisa",
    waiting: "esperando você decidir algo", off: "fora do ar" }[mode];
  $("mood-line").textContent = doing || pet.mood;
}

async function loadGrowth() {
  const g = await api("/api/growth");
  pet.view?.setStage(g.stage);
  pet.mini?.setStage(g.stage);
  $("pet-stage").textContent = g.name;
  $("pet-stage").title = g.description;
  $("pet-bar").firstElementChild.style.width = `${Math.round(g.progress * 100)}%`;
  const c = g.counts;
  $("pet-bar").title = `${g.xp} de convivência: ${c.days} dias de conversa, ${c.user_messages} mensagens suas, `
    + `${c.goals_done} objetivos concluídos, ${c.dreams} sonhos, ${c.confirmed_memories} lembranças confirmadas, `
    + `${c.creations ?? 0} criações, ${c.lessons ?? 0} lições. ${g.note}`;
  $("pet-next").textContent = g.next_name ? `${g.xp}/${g.next_at} até ${g.next_name}` : `${g.xp} de convivência`;
}

$("quick").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = $("quick-text").value.trim();
  if (!text) return;
  try {
    await api("/api/tasks/goals", { method: "POST", body: JSON.stringify({ description: text }) });
    $("quick-text").value = "";
    addMessage("note", `Pedido registrado: "${text}". Acompanhe em "Fazendo".`);
    await refreshPanel();
  } catch (err) { addMessage("error", err.message); }
});

// Painel "Agora": fixo ao lado em telas largas; em telas estreitas abre por cima, tocando no Nym do topo.
function setPanel(open) {
  $("panel").classList.toggle("open", open);
  $("backdrop").hidden = !open;
  $("toggle-panel").setAttribute("aria-expanded", String(open));
  if (open) $("close-panel").focus();
}
$("toggle-panel").addEventListener("click", () => setPanel(!$("panel").classList.contains("open")));
$("close-panel").addEventListener("click", () => { setPanel(false); $("toggle-panel").focus(); });
$("backdrop").addEventListener("click", () => setPanel(false));
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && $("panel").classList.contains("open")) setPanel(false); });

// --- presença: ele pode puxar assunto ------------------------------------------------------------------------
let presenceBusy = false;
async function presenceTick(opened) {
  if (presenceBusy || state.streaming) return;
  presenceBusy = true;
  try {
    const data = await api("/api/presence/tick", { method: "POST", body: JSON.stringify({ opened }) });
    if (data.message) {
      const node = addMessage("assistant", data.message.content, "ele puxou assunto", "initiative");
      addReactions(node, data.message.id, null);
      notify(data.message.content);
      refreshPanel();
    }
  } catch { /* opcional */ }
  finally { presenceBusy = false; }
}

// --- notificações do navegador: quando a aba está escondida e ele chama -------------------------------------------
function notifySupported() { return "Notification" in window; }

function notify(text) {
  if (!document.hidden || !notifySupported() || Notification.permission !== "granted") return;
  try {
    const n = new Notification("Nym", { body: text.length > 180 ? `${text.slice(0, 177)}…` : text, tag: "nym-presence" });
    n.onclick = () => { window.focus(); n.close(); };
  } catch { /* alguns navegadores só permitem via service worker; a mensagem já está no chat */ }
}

function updateNotifyButton() {
  const b = $("notify");
  b.hidden = !notifySupported() || Notification.permission !== "default";
}

$("notify").addEventListener("click", async () => {
  try { await Notification.requestPermission(); } catch { /* recusado */ }
  updateNotifyButton();
});
updateNotifyButton();

// --- envio ---------------------------------------------------------------------------------------------------
els.form.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const text = els.input.value.trim();
  if (state.streaming || (!text && !window.nymSenses?.has())) return;
  const images = window.nymSenses?.take() || [];
  const finalText = text || "Olha isso.";
  els.input.value = "";
  els.input.style.height = "auto";
  send(finalText, images);
});
els.input.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); els.form.requestSubmit(); }
});
els.input.addEventListener("input", () => {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 160)}px`;
});
els.stop.addEventListener("click", async () => {
  try { await fetch("/api/chat/stop", { method: "POST" }); } catch { /* segue */ }
  setTimeout(() => state.controller?.abort(), 1500);   // se o servidor não fechar sozinho, corta do lado de cá
});

loadHistory().then(() => presenceTick(true));
refreshPanel();
setInterval(refreshPanel, 10000);
setInterval(() => presenceTick(false), 30000);
