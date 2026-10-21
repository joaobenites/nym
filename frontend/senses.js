"use strict";
// Sentidos (Fase 8): mostrar imagens, a câmera ou a tela para o Nym. Tudo começa com um clique seu: a câmera abre,
// tira UMA foto quando você manda e desliga; a tela é capturada uma vez e o compartilhamento para na hora.
// As imagens são reduzidas aqui (no máximo 1024 px) antes de sair do navegador.
(() => {
  const $ = (id) => document.getElementById(id);
  const MAX_SIDE = 1024;
  const MAX = 3;
  const pending = [];
  let status = null;
  let camStream = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function toDataUrl(source, w, h) {
    const scale = Math.min(1, MAX_SIDE / Math.max(w, h));
    const c = document.createElement("canvas");
    c.width = Math.round(w * scale);
    c.height = Math.round(h * scale);
    c.getContext("2d").drawImage(source, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.85);
  }

  function render() {
    const tray = $("tray");
    tray.hidden = !pending.length;
    tray.replaceChildren(...pending.map((p, i) => {
      const box = el("div", "shot");
      const img = el("img");
      img.src = p.data;
      img.alt = p.name;
      const x = el("button", "ghost", "✕");
      x.type = "button";
      x.title = "Tirar";
      x.addEventListener("click", () => { pending.splice(i, 1); render(); });
      box.append(img, el("span", "kind", { camera: "câmera", screen: "tela" }[p.kind] || "imagem"), x);
      return box;
    }));
  }

  function add(kind, data, name) {
    if (pending.length >= MAX) { window.nymNotice?.(`No máximo ${MAX} imagens por mensagem.`); return; }
    pending.push({ kind, data, name });
    render();
  }

  function fromFile(file) {
    if (!file || !file.type.startsWith("image/")) return;
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { add("image", toDataUrl(img, img.naturalWidth, img.naturalHeight), file.name || "imagem"); URL.revokeObjectURL(url); };
    img.onerror = () => { window.nymNotice?.("Não consegui abrir essa imagem."); URL.revokeObjectURL(url); };
    img.src = url;
  }

  function stopCamera() {
    camStream?.getTracks().forEach((t) => t.stop());
    camStream = null;
    $("cam-video").srcObject = null;
  }

  async function openCamera() {
    try {
      camStream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 } }, audio: false });
    } catch (err) {
      window.nymNotice?.(`A câmera não abriu (${err.name === "NotAllowedError" ? "permissão negada" : err.message}).`);
      return;
    }
    $("cam-video").srcObject = camStream;
    $("camera").showModal();
  }

  async function grabScreen() {
    let stream;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
    } catch (err) {
      if (err.name !== "NotAllowedError") window.nymNotice?.(`Não deu para capturar a tela (${err.message}).`);
      return;
    }
    try {
      const v = document.createElement("video");
      v.muted = true;
      v.srcObject = stream;
      await v.play();
      await new Promise((r) => setTimeout(r, 300));            // deixa o primeiro quadro chegar
      add("screen", toDataUrl(v, v.videoWidth, v.videoHeight), "tela");
    } finally {
      stream.getTracks().forEach((t) => t.stop());              // o compartilhamento para logo depois do quadro
    }
  }

  async function loadStatus() {
    try {
      const res = await fetch("/api/senses");
      status = await res.json();
    } catch { status = null; }
    const ok = Boolean(status?.vision?.available);
    for (const id of ["sense-image", "sense-camera", "sense-screen"]) {
      const b = $(id);
      b.disabled = !ok;
      if (!ok) b.title = `Ele ainda não enxerga: ${status?.vision?.detail || "servidor fora do ar"}`;
    }
    if (!navigator.mediaDevices?.getUserMedia) $("sense-camera").hidden = true;
    if (!navigator.mediaDevices?.getDisplayMedia) $("sense-screen").hidden = true;
  }

  // Celular: os botões ficam atrás de um "+" para sobrar espaço para escrever.
  const group = $("senses");
  const more = $("sense-more");
  const closeMore = () => { group.classList.remove("open"); more.setAttribute("aria-expanded", "false"); };
  more.addEventListener("click", () => {
    const open = !group.classList.contains("open");
    group.classList.toggle("open", open);
    more.setAttribute("aria-expanded", String(open));
  });
  group.addEventListener("click", (ev) => { if (ev.target.closest("button")) closeMore(); });
  document.addEventListener("click", (ev) => { if (!ev.target.closest("#senses, #sense-more")) closeMore(); });
  $("sense-image").addEventListener("click", () => $("sense-file").click());
  $("sense-file").addEventListener("change", (ev) => { [...ev.target.files].forEach(fromFile); ev.target.value = ""; });
  $("sense-camera").addEventListener("click", openCamera);
  $("sense-screen").addEventListener("click", grabScreen);
  $("cam-shot").addEventListener("click", () => {
    const v = $("cam-video");
    if (v.videoWidth) add("camera", toDataUrl(v, v.videoWidth, v.videoHeight), "câmera");
    $("camera").close();
  });
  $("cam-close").addEventListener("click", () => $("camera").close());
  $("camera").addEventListener("close", stopCamera);            // fechou de qualquer jeito: câmera desliga
  const ok = () => Boolean(status?.vision?.available);
  document.addEventListener("paste", (ev) => {
    if (!ok()) return;
    for (const item of ev.clipboardData?.items || []) if (item.type.startsWith("image/")) fromFile(item.getAsFile());
  });
  const chat = document.querySelector(".chat");
  chat.addEventListener("dragover", (ev) => { if (ok() && [...ev.dataTransfer.types].includes("Files")) ev.preventDefault(); });
  chat.addEventListener("drop", (ev) => {
    if (!ok()) return;
    ev.preventDefault();
    [...ev.dataTransfer.files].forEach(fromFile);
  });

  window.nymSenses = {
    take() { const out = pending.splice(0).map(({ kind, data, name }) => ({ kind, data, name })); render(); return out; },
    has: () => pending.length > 0,
    refresh: loadStatus,
  };
  loadStatus();
})();
