"use strict";
// Barra lateral comum a todas as páginas: navegação, avisos (pedidos de permissão, propostas) e estado do modelo.
(() => {
  const PAGES = [
    ["index.html", "💬", "Conversa"],
    ["tasks.html", "✔", "Tarefas", "tasks"],
    ["atelier.html", "✎", "Ateliê", "atelier"],
    ["link.html", "◉", "PET"],
    ["pet.html", "♥", "Cuidados"],
    ["life.html", "☾", "Vida"],
    ["memory.html", "◈", "Memória"],
    ["identity.html", "☺", "Identidade", "identity"],
  ];
  const here = location.pathname.split("/").pop() || "index.html";
  const rail = document.getElementById("rail");
  if (!rail) return;
  rail.className = "rail";
  rail.setAttribute("aria-label", "Navegação");

  const brand = document.createElement("div");
  brand.className = "brand";
  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.id = "avatar";
  const name = document.createElement("b");
  name.textContent = "Nym";
  brand.append(avatar, name);
  rail.append(brand);

  for (const [href, ico, label, badgeId] of PAGES) {
    const a = document.createElement("a");
    a.className = "nav";
    a.href = href;
    if (href === here) a.setAttribute("aria-current", "page");
    const i = document.createElement("span");
    i.className = "ico";
    i.textContent = ico;
    i.setAttribute("aria-hidden", "true");
    const t = document.createElement("span");
    t.className = "label";
    t.textContent = label;
    a.append(i, t);
    if (badgeId) {
      const b = document.createElement("span");
      b.className = "badge";
      b.id = `badge-${badgeId}`;
      b.hidden = true;
      a.append(b);
    }
    rail.append(a);
  }

  const status = document.createElement("div");
  status.className = "status";
  const dot = document.createElement("span");
  dot.className = "dot";
  dot.id = "status-dot";
  const st = document.createElement("span");
  st.id = "status-text";
  st.textContent = "verificando…";
  status.append(dot, st);
  rail.append(status);

  // --- celular (Fase 8.1): parear pela rede local; só aparece no próprio computador -----------------------------
  async function setupPhone() {
    let info;
    try { info = await json("/api/access"); } catch { return; }
    if (!info.local) {
      if (info.device) document.body.classList.add("remote");
      return;
    }
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ghost phone-btn";
    btn.textContent = "📱 Celular";
    btn.title = "Usar o Nym no celular (mesmo Wi-Fi)";
    rail.insertBefore(btn, status);
    const dlg = document.createElement("dialog");
    dlg.className = "viewer phone-dialog";
    document.body.append(dlg);
    let timer = null;
    const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };

    async function render() {
      const data = await json("/api/access");
      const head = el("div", "viewer-head");
      const close = el("button", "ghost", "✕");
      close.type = "button";
      close.setAttribute("aria-label", "Fechar");
      close.addEventListener("click", () => dlg.close());
      head.append(el("b", "", "Usar no celular"), close);
      const body = [head];
      if (!data.lan) {
        body.push(el("p", "prose-line", "Hoje o Nym só escuta neste computador. Para abrir no celular (no mesmo Wi-Fi):"),
          el("ol", "steps-list"));
        const ol = body[body.length - 1];
        for (const t of ["No arquivo .env da pasta do Nym, coloque a linha NYM_LAN=true.",
          "Feche e abra o Nym de novo. Se o Windows perguntar sobre o firewall, permita em 'Redes privadas'.",
          "Volte aqui: vai aparecer o endereço e o código para o celular."]) ol.append(el("li", "", t));
      } else {
        body.push(el("p", "prose-line", "No celular, no mesmo Wi-Fi, abra:"));
        for (const u of data.urls.length ? data.urls : ["(não achei o IP deste computador)"]) body.push(el("p", "big-url", u));
        const code = el("p", "pair-code", "");
        const gen = el("button", "primary", "Gerar código para o celular");
        gen.type = "button";
        gen.addEventListener("click", async () => {
          const r = await fetch("/api/access/code", { method: "POST" }).then((x) => x.json());
          let left = r.expires_in;
          clearInterval(timer);
          const tick = () => {
            code.textContent = `${r.code.slice(0, 3)} ${r.code.slice(3)}`;
            code.dataset.left = `vale por ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
            if (left-- <= 0) { clearInterval(timer); code.textContent = "vencido"; code.dataset.left = ""; }
          };
          tick();
          timer = setInterval(tick, 1000);
        });
        body.push(gen, code, el("p", "kind prose", "O código vale uma vez, por 10 minutos. Câmera e tela do celular não funcionam pela rede local (o navegador exige endereço seguro); o botão de imagem abre a câmera do celular para tirar foto."));
      }
      body.push(el("h3", "", "Aparelhos conectados"));
      const list = el("div", "");
      for (const d of data.devices) {
        const row = el("div", "item row-inline");
        const seen = d.last_seen ? new Date(d.last_seen).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "";
        row.append(el("span", "grow", `${d.name}`), el("span", "kind", `visto ${seen}`));
        const x = el("button", "danger", "Desconectar");
        x.type = "button";
        x.addEventListener("click", async () => { await fetch(`/api/access/devices/${d.id}`, { method: "DELETE" }); render(); });
        row.append(x);
        list.append(row);
      }
      if (!data.devices.length) list.append(el("p", "kind", "Nenhum ainda."));
      body.push(list);
      dlg.replaceChildren(...body);
    }
    btn.addEventListener("click", async () => { await render(); dlg.showModal(); });
    dlg.addEventListener("close", () => clearInterval(timer));
  }

  async function json(path) {
    const res = await fetch(path);
    if (res.status === 401) { location.href = "/login.html"; throw new Error("não pareado"); }   // aparelho desconectado
    if (!res.ok) throw new Error(String(res.status));
    return res.json();
  }

  function setBadge(id, n, title) {
    const b = document.getElementById(`badge-${id}`);
    if (!b) return;
    b.hidden = !n;
    b.textContent = n ? String(n) : "";
    b.title = title || "";
  }

  async function refresh() {
    try {
      const h = await json("/health");
      const ok = h.database.ok && h.model.available;
      dot.dataset.state = !h.database.ok ? "bad" : ok ? "ok" : "warn";
      st.textContent = !h.database.ok ? "banco com problema" : ok ? h.model.name : "modelo indisponível";
      st.title = h.model.detail || "";
      if (!document.body.classList.contains("busy")) avatar.dataset.state = ok ? "" : "off";
      document.body.dataset.online = ok ? "1" : "0";
    } catch {
      dot.dataset.state = "bad";
      st.textContent = "servidor fora do ar";
      avatar.dataset.state = "off";
      document.body.dataset.online = "0";
    }
    try {
      const t = await json("/api/tasks");
      setBadge("tasks", t.awaiting_permission.length + t.goals.filter((g) => g.status === "proposed").length,
        "pedidos de permissão e ideias dele esperando você");
    } catch { /* opcional */ }
    try {
      const p = await json("/api/proposals");
      setBadge("atelier", p.proposals.filter((x) => x.status === "proposed").length, "melhorias de código esperando você");
    } catch { /* opcional */ }
    try {
      const idn = await json("/api/identity");
      setBadge("identity", idn.pending_changes, "propostas de identidade pendentes");
    } catch { /* opcional */ }
  }

  setupPhone();
  window.nymShell = { refresh, setBusy: (busy) => { avatar.dataset.state = busy ? "busy" : ""; document.body.classList.toggle("busy", busy); } };
  refresh();
  setInterval(refresh, 20000);
})();
