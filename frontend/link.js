"use strict";
/* Tela PET (parceria estilo operador/navi): versão compacta para o celular. Só usa rotas que já existem;
 * os "chips" são frases prontas enviadas ao chat comum, não comandos especiais. */

const $ = (id) => document.getElementById(id);
const face = window.NymAvatar?.create($("face"));
const CHIPS = [["Status", "Como você está agora? Resumo curto."], ["Seu dia", "O que você fez hoje sozinho?"],
  ["Missão", "Tenho uma missão pra nós dois. Pronto?"], ["Preciso de você", "Preciso da sua ajuda com uma coisa."]];
let busy = false;

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  let data = null;
  try { data = await res.json(); } catch { /* sem corpo */ }
  if (!res.ok) throw new Error(data?.detail || `Erro HTTP ${res.status}`);
  return data;
}

function mode(m) { face?.setMode(m); }

async function refresh() {
  try {
    const [g, p] = await Promise.all([api("/api/growth"), api("/api/pet")]);
    face?.setStage(g.stage);
    $("stage").textContent = `Nym · ${g.name}`;
    const calls = p.open_calls.length ? ` · chamando: ${p.open_calls.map((c) => c.need).join(", ")}` : "";
    $("status").textContent = `${p.mood}${calls}`;
    if (!busy) mode(p.sleeping ? "dreaming" : p.open_calls.length ? "waiting" : "idle");
    document.body.dataset.online = "1";
  } catch (err) {
    mode("off");
    $("status").textContent = "fora do ar";
  }
}

async function lastMessage() {
  const h = await api("/api/chat/history?limit=1");
  const last = h.messages[h.messages.length - 1];
  $("said").textContent = last ? last.content : "Oi! Ainda não conversamos.";
}

async function send(text) {
  if (busy || !text.trim()) return;
  busy = true;
  $("error").textContent = "";
  $("said").textContent = "";
  mode("thinking");
  try {
    const res = await fetch("/api/chat/stream", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text.trim(), images: [] }) });
    if (!res.ok || !res.body) throw new Error((await res.json().catch(() => ({}))).detail || `Erro HTTP ${res.status}`);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.type === "delta") { mode("talking"); $("said").textContent += ev.text; }
        else if (ev.type === "error") throw new Error(ev.detail || "Falha do modelo.");
      }
    }
  } catch (err) {
    $("error").textContent = err.message;
  } finally {
    busy = false;
    mode("idle");
    refresh();
  }
}

for (const [label, text] of CHIPS) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  b.addEventListener("click", () => send(text));
  $("chips").append(b);
}
$("form").addEventListener("submit", (e) => {
  e.preventDefault();
  const t = $("text").value;
  $("text").value = "";
  send(t);
});

async function tick(opened) {
  if (busy) return;
  try {
    const m = await api("/api/presence/tick", { method: "POST", body: JSON.stringify({ opened }) });
    if (m && m.message && m.message.content) { $("said").textContent = m.message.content; mode("talking"); setTimeout(() => mode("idle"), 4000); }
  } catch { /* sem iniciativa agora */ }
}

lastMessage().catch(() => { $("said").textContent = ""; });
refresh();
tick(true);
setInterval(refresh, 30000);
setInterval(() => tick(false), 60000);
