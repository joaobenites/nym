"use strict";
// Ateliê (Fase 7): criações do Nym, propostas de código e lições. Conteúdo do Nym nunca vira HTML desta página:
// desenhos aparecem como <img> (SVG em imagem não executa nada), brinquedos num iframe isolado sem rede, e
// textos/código como texto puro.

const $ = (id) => document.getElementById(id);
const KIND = { drawing: "desenho", text: "texto", toy: "brinquedo", code: "código" };
const TOY_CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
  + "font-src data:; media-src data: blob:; connect-src 'none'; form-action 'none'";
let filter = "";
let current = null;

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) {
    let detail = `Erro HTTP ${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch { /* sem corpo */ }
    throw new Error(typeof detail === "string" ? detail : "Pedido inválido.");
  }
  return res.json();
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function showError(err) { $("error").textContent = err ? err.message || String(err) : ""; }
function when(iso) { return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }); }

function svgImage(svg) {
  const img = el("img", "art");
  img.alt = "Desenho do Nym";
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  return img;
}

function toyFrame(html) {
  const f = document.createElement("iframe");
  f.className = "toy";
  f.setAttribute("sandbox", "allow-scripts");          // sem allow-same-origin: não enxerga esta página nem a API
  f.setAttribute("referrerpolicy", "no-referrer");
  f.title = "Brinquedo do Nym (isolado, sem internet)";
  f.srcdoc = `<meta http-equiv="Content-Security-Policy" content="${TOY_CSP}">` + html;
  return f;
}

// --- abas ---------------------------------------------------------------------------------------------------------
document.querySelectorAll("[role=tab]").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("[role=tab]").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
  for (const t of ["gallery", "proposals", "lessons"]) $(`tab-${t}`).hidden = t !== b.dataset.tab;
  load(b.dataset.tab);
}));
document.querySelectorAll(".filters button").forEach((b) => b.addEventListener("click", () => {
  filter = b.dataset.kind;
  document.querySelectorAll(".filters button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  loadGallery();
}));

// --- galeria ------------------------------------------------------------------------------------------------------
async function loadGallery() {
  const data = await api(`/api/creations${filter ? `?kind=${filter}` : ""}`);
  const cards = await Promise.all(data.creations.map(async (c) => {
    const card = el("button", "art-card");
    card.type = "button";
    const thumb = el("div", `thumb ${c.kind}`);
    if (c.kind === "drawing") {
      try { thumb.append(svgImage((await api(`/api/creations/${c.id}`)).content)); } catch { thumb.textContent = "✎"; }
    } else {
      thumb.textContent = { text: "❝", toy: "▶", code: "{ }" }[c.kind];
    }
    const mood = c.reaction === 1 ? " · 👍" : c.reaction === -1 ? " · 👎" : "";
    card.append(thumb, el("b", "", c.title),
      el("span", "kind", `${KIND[c.kind]}${c.version > 1 ? ` · v${c.version}` : ""} · ${when(c.created_at)}${mood}`));
    card.addEventListener("click", () => open(c.id).catch(showError));
    return card;
  }));
  $("gallery").replaceChildren(...(cards.length ? cards
    : [el("p", "kind prose", "Nada ainda. Ele cria quando tem um tempo livre, ou peça no painel do chat: \"faz um desenho de...\".")]));
}

async function open(id) {
  const c = await api(`/api/creations/${id}`);
  current = c;
  $("v-title").textContent = c.title;
  $("v-meta").textContent = `${KIND[c.kind]} · v${c.version} · ${when(c.created_at)}`;
  $("v-about").textContent = c.about ? `O que ele quis fazer: ${c.about}` : "";
  let body;
  if (c.kind === "drawing") body = svgImage(c.content);
  else if (c.kind === "toy") body = toyFrame(c.content);
  else body = el("pre", c.kind === "code" ? "code" : "prose-text", c.content);
  $("v-body").replaceChildren(body);
  $("v-versions").textContent = c.versions.length > 1 ? `${c.versions.length} versões` : "";
  $("v-comment").value = "";
  markReaction(c.reaction);
  $("viewer").showModal();
}

function markReaction(v) {
  $("v-up").classList.toggle("primary", v === 1);
  $("v-down").classList.toggle("primary", v === -1);
}

async function react(value) {
  if (!current) return;
  const v = current.reaction === value ? 0 : value;     // clicar de novo tira a reação
  await api("/api/feedback", { method: "POST", body: JSON.stringify({ target: "creation", id: current.id, value: v, comment: $("v-comment").value || null }) });
  current.reaction = v || null;
  markReaction(current.reaction);
  loadGallery();
}
$("v-up").addEventListener("click", () => react(1).catch(showError));
$("v-down").addEventListener("click", () => react(-1).catch(showError));
$("v-close").addEventListener("click", () => { $("viewer").close(); $("v-body").replaceChildren(); });
$("viewer").addEventListener("close", () => $("v-body").replaceChildren());   // para o brinquedo de rodar
$("v-archive").addEventListener("click", async () => {
  try {
    await api(`/api/creations/${current.id}/archive`, { method: "POST" });
    $("viewer").close();
    loadGallery();
  } catch (err) { showError(err); }
});

// --- propostas de código ----------------------------------------------------------------------------------------
function diffView(diff) {
  const pre = el("pre", "diff");
  for (const line of diff.split("\n")) {
    const cls = line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del"
      : line.startsWith("@@") ? "hunk" : "";
    pre.append(el("span", `ln ${cls}`.trim(), line || " "));
  }
  return pre;
}

const STATUS = { proposed: "esperando você", applied: "aplicada (reinicie o Nym)", rejected: "recusada", reverted: "desfeita", stale: "desatualizada (o arquivo mudou)" };

async function decide(id, action, label) {
  if (action === "apply" && !confirm(`Aplicar a melhoria #${id} no código do Nym? Uma cópia do arquivo original fica guardada para desfazer.`)) return;
  const r = await api(`/api/proposals/${id}/${action}`, { method: "POST" });
  if (r.restart_needed) $("error").textContent = `${label}: reinicie o Nym para valer (feche e abra de novo).`;
  await loadProposals();
  window.nymShell?.refresh();
}

async function loadProposals() {
  const data = await api("/api/proposals");
  const pending = data.proposals.filter((p) => p.status === "proposed").length;
  $("n-proposals").hidden = !pending;
  $("n-proposals").textContent = pending || "";
  const items = data.proposals.map((p) => {
    const box = el("div", `item${p.status === "proposed" ? " pending" : ""}`);
    box.append(el("b", "", `#${p.id} ${p.title}`), el("div", "kind", `${p.file} · ${STATUS[p.status]} · ${when(p.created_at)}`),
      el("p", "prose-line", p.why));
    if (p.warnings.length) box.append(el("p", "warn-line", `Atenção: ${p.warnings.join("; ")}`));
    const details = el("details");
    details.append(el("summary", "", "Ver o que muda"), diffView(p.diff));
    details.open = p.status === "proposed";
    box.append(details);
    const row = el("div", "row");
    const btn = (label, cls, action) => {
      const b = el("button", cls, label);
      b.type = "button";
      b.addEventListener("click", async () => {
        b.disabled = true;
        try { showError(null); await decide(p.id, action, label); } catch (err) { showError(err); } finally { b.disabled = false; }
      });
      return b;
    };
    if (p.status === "proposed") row.append(btn("Aplicar", "primary", "apply"), btn("Recusar", "danger", "reject"));
    if (p.status === "applied") row.append(btn("Desfazer", "", "revert"));
    if (row.childElementCount) box.append(row);
    return box;
  });
  $("proposals").replaceChildren(...(items.length ? items : [el("p", "kind prose", "Nenhuma proposta ainda.")]));
}

// --- lições -------------------------------------------------------------------------------------------------------
async function loadLessons() {
  const data = await api("/api/lessons");
  for (const scope of ["task", "chat"]) {
    const rows = data.lessons.filter((l) => l.scope === scope).map((l) => {
      const box = el("div", "item");
      if (l.status === "retired") box.classList.add("retired");
      box.append(el("div", "", l.text));
      const extra = l.reinforced ? ` · tirada de novo ${l.reinforced}x` : "";
      const tools = l.tools.length ? ` · ${l.tools.join(", ")}` : "";
      box.append(el("div", "kind", `ajudou ${l.wins}x · falhou ${l.losses}x · usada ${l.uses}x${extra}${tools}`
        + (l.status === "retired" ? ` · aposentada: ${l.retired_reason || ""}` : "")));
      const b = el("button", "", l.status === "active" ? "Aposentar" : "Reativar");
      b.type = "button";
      b.addEventListener("click", async () => {
        try { await api(`/api/lessons/${l.id}/${l.status === "active" ? "retire" : "restore"}`, { method: "POST" }); loadLessons(); }
        catch (err) { showError(err); }
      });
      const row = el("div", "row");
      row.append(b);
      box.append(row);
      return box;
    });
    $(`lessons-${scope}`).replaceChildren(...(rows.length ? rows
      : [el("p", "kind prose", scope === "task" ? "Ainda nenhuma: elas aparecem quando ele termina objetivos."
        : "Ainda nenhuma: reaja às respostas dele no chat (👍/👎) para ele aprender.")]));
  }
}

async function load(tab) {
  showError(null);
  try {
    if (tab === "gallery") await loadGallery();
    else if (tab === "proposals") await loadProposals();
    else await loadLessons();
  } catch (err) { showError(err); }
}

load("gallery");
loadProposals().catch(() => {});
