"use strict";
// Pareamento do celular: o código vem do computador do Nym; o servidor devolve um cookie que só ele lê.
const $ = (id) => document.getElementById(id);
$("name").value = /iPhone|iPad/.test(navigator.userAgent) ? "iPhone" : /Android/.test(navigator.userAgent) ? "Celular Android" : "";
$("pair").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("msg").textContent = "";
  const btn = ev.target.querySelector("button");
  btn.disabled = true;
  try {
    const res = await fetch("/api/pair", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: $("code").value.replace(/\D/g, ""), name: $("name").value.trim() || null }) });
    if (res.ok) { location.href = "/"; return; }
    let detail = `Erro ${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch { /* sem corpo */ }
    $("msg").textContent = typeof detail === "string" ? detail : "Código inválido.";
  } catch {
    $("msg").textContent = "Não consegui falar com o computador do Nym. Ele está ligado e no mesmo Wi-Fi?";
  } finally { btn.disabled = false; }
});
