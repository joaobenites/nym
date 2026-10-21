"use strict";

const $ = (id) => document.getElementById(id);
const BARS = [["fullness", "Barriguinha"], ["fun", "Diversão"], ["hygiene", "Higiene"], ["affection", "Carinho"], ["discipline", "Disciplina"]];
const EVENT = { feed: "comeu", snack: "ganhou um petisco", play: "brincou", clean: "foi limpo", train: "treinou", pet: "recebeu carinho",
  sleep: "foi dormir", wake: "acordou", pause: "modo jogo ligado", resume: "modo jogo desligado", mess: "fez uma sujeirinha",
  care_mistake: "ficou esperando cuidado", refused: "recusou" };
let current = null;

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  let data = null;
  try { data = await res.json(); } catch { /* sem corpo */ }
  if (!res.ok) throw new Error(Array.isArray(data?.detail) ? "Valor inválido." : (data?.detail || `Erro HTTP ${res.status}`));
  return data;
}

function el(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text !== undefined) n.textContent = text;   // textContent: nunca interpreta HTML
  return n;
}

async function run(fn) {
  $("error").textContent = "";
  try { await fn(); } catch (err) { $("error").textContent = err.message; }
}

function act(action, body = {}) {
  return run(async () => {
    const r = await api(`/api/pet/${action}`, { method: "POST", body: JSON.stringify(body) });
    $("reply").textContent = `Nym: ${r.message}`;
    render(r.pet);
  });
}

function addButton(parent, label, handler) {
  const b = el("button", "", label);
  b.type = "button";
  b.addEventListener("click", handler);
  parent.append(b);
}

function render(p) {
  current = p;
  $("mood").textContent = `Humor: ${p.mood}`;
  $("sub").textContent = p.traits.length ? `Jeito: ${p.traits.join(", ")}` : "Ainda sem um jeito definido pelos cuidados.";
  const meters = $("meters");
  meters.replaceChildren();
  for (const [key, label] of BARS) {
    const row = el("div", "meter");
    const bar = el("div", "bar");
    const fill = el("span");
    fill.style.width = `${Math.round(p[key])}%`;
    bar.append(fill);
    row.append(el("span", "", label), bar, el("span", "", String(Math.round(p[key]))));
    meters.append(row);
  }
  $("messes").textContent = p.messes ? `Sujeirinhas por limpar: ${"💩".repeat(Math.min(p.messes, 6))}` : "";
  $("calls").textContent = p.open_calls.length ? `Está chamando: ${p.open_calls.map((c) => c.need).join(", ")}` : "";
  const actions = $("actions");
  actions.replaceChildren();
  const asleep = p.sleeping;
  for (const [a, label] of [["feed", "Refeição"], ["snack", "Petisco"], ["clean", "Limpar"], ["train", "Treinar"], ["pet", "Carinho"]]) {
    addButton(actions, label, () => act(a));
  }
  addButton(actions, asleep ? "Acordar" : "Dormir 6 h", () => (asleep ? act("wake") : act("sleep", { hours: 6 })));
  const rps = $("rps");
  rps.replaceChildren();
  for (const c of ["pedra", "papel", "tesoura"]) addButton(rps, c, () => act("play", { choice: c }));
  const pause = $("pause");
  pause.replaceChildren();
  addButton(pause, p.paused ? "Voltar do modo jogo" : "Ligar modo jogo (pausa)", () => act(p.paused ? "resume" : "pause"));
  const events = $("events");
  events.replaceChildren();
  for (const e of p.recent_events) {
    const extra = e.kind === "play" && e.detail.result ? ` (você: ${e.detail.you}, ele: ${e.detail.nym})` : "";
    events.append(el("div", "kind", `${new Date(e.created_at).toLocaleString("pt-BR")} · ${EVENT[e.kind] || e.kind}${extra}`));
  }
}

async function load() { await run(async () => render(await api("/api/pet"))); }

load();
setInterval(load, 60000);
