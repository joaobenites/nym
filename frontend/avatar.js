/* Avatar do Nym (Fase 6.2): criatura original em SVG que mostra o ESTADO REAL do sistema.
 *
 * Forma = estágio de convivência (GET /api/growth, calculado de dados reais; só sobe).
 * Modo  = o que está acontecendo de verdade: respondendo (streaming), sonhando (ciclo do Somnium rodando),
 *         esperando você (pedido de permissão ou ideia pendente), trabalhando (objetivo ativo), fora do ar
 *         (servidor ou modelo indisponível). Nada aqui é sorteado: se o modo muda, é porque o backend mudou.
 */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const MODES = {
    idle: "à toa",
    thinking: "pensando na resposta",
    talking: "falando com você",
    dreaming: "sonhando (Somnium)",
    working: "trabalhando num objetivo",
    waiting: "esperando você decidir algo",
    off: "fora do ar",
  };

  // Partes por estágio: 0 ovo · 1 filhote · 2 aprendiz · 3 parceiro · 4 companheiro.
  function body(stage) {
    if (stage === 0) {
      return `
        <ellipse class="shadow" cx="60" cy="108" rx="26" ry="5"/>
        <path class="shell" d="M60 22 C38 22 30 58 30 76 C30 95 44 106 60 106 C76 106 90 95 90 76 C90 58 82 22 60 22 Z"/>
        <path class="crack" d="M36 66 L46 60 L52 68 L60 58 L68 68 L75 60 L84 66"/>
        <g class="spots"><circle cx="48" cy="40" r="4"/><circle cx="73" cy="86" r="5"/><circle cx="80" cy="46" r="3"/></g>
        <g class="face" transform="translate(0 2)">${eyes(52, 68, 76, 3.2)}</g>`;
    }
    const tall = stage >= 2;
    const top = tall ? 30 : 40;
    const parts = [];
    parts.push(`<ellipse class="shadow" cx="60" cy="108" rx="${stage >= 3 ? 30 : 24}" ry="5"/>`);
    if (stage >= 4) parts.push(`<path class="tail" d="M84 92 Q104 96 102 78 Q100 70 94 74"/>`);
    // antenas
    parts.push(`<path class="antenna" d="M60 ${top + 2} Q58 ${top - 12} 50 ${top - 18}"/><circle class="tip" cx="50" cy="${top - 18}" r="4"/>`);
    if (tall) parts.push(`<path class="antenna" d="M66 ${top + 3} Q70 ${top - 10} 78 ${top - 14}"/><circle class="tip" cx="78" cy="${top - 14}" r="3.4"/>`);
    // orelhas/barbatanas
    if (stage >= 3) parts.push(`<path class="fin" d="M30 ${top + 30} L18 ${top + 18} L28 ${top + 44} Z"/><path class="fin" d="M90 ${top + 30} L102 ${top + 18} L92 ${top + 44} Z"/>`);
    // corpo
    parts.push(`<path class="skin" d="M60 ${top} C${tall ? 34 : 36} ${top} 28 ${top + 26} 28 ${top + 44} C28 ${106 - 4} 42 104 60 104 C78 104 92 ${106 - 4} 92 ${top + 44} C92 ${top + 26} ${tall ? 86 : 84} ${top} 60 ${top} Z"/>`);
    parts.push(`<ellipse class="belly" cx="60" cy="${top + 52}" rx="${tall ? 18 : 15}" ry="${tall ? 16 : 13}"/>`);
    // bracinhos
    if (tall) parts.push(`<path class="arm" d="M30 ${top + 46} Q20 ${top + 54} 24 ${top + 62}"/><path class="arm arm-r" d="M90 ${top + 46} Q100 ${top + 54} 96 ${top + 62}"/>`);
    // faixa/visor dos estágios maiores
    if (stage >= 3) parts.push(`<path class="band" d="M34 ${top + 18} Q60 ${top + 10} 86 ${top + 18}"/>`);
    const ey = top + 26;
    parts.push(`<g class="face">${eyes(48, 72, ey, stage === 1 ? 5.2 : 4.6)}
      <path class="mouth" d="M53 ${ey + 13} Q60 ${ey + 18} 67 ${ey + 13}"/>
      <ellipse class="mouth-open" cx="60" cy="${ey + 15}" rx="5" ry="3.5"/>
      <g class="cheeks"><ellipse cx="40" cy="${ey + 10}" rx="4" ry="2.4"/><ellipse cx="80" cy="${ey + 10}" rx="4" ry="2.4"/></g></g>`);
    if (stage >= 4) parts.push(`<ellipse class="halo" cx="60" cy="${top - 30}" rx="12" ry="3.4"/>`);
    return parts.join("");
  }

  function eyes(lx, rx, y, r) {
    return `<g class="eyes">
      <g class="eye"><circle cx="${lx}" cy="${y}" r="${r}"/><circle class="glint" cx="${lx + r / 3}" cy="${y - r / 3}" r="${r / 3}"/></g>
      <g class="eye"><circle cx="${rx}" cy="${y}" r="${r}"/><circle class="glint" cx="${rx + r / 3}" cy="${y - r / 3}" r="${r / 3}"/></g></g>
      <g class="eyes-closed"><path d="M${lx - r} ${y} Q${lx} ${y + r} ${lx + r} ${y}"/><path d="M${rx - r} ${y} Q${rx} ${y + r} ${rx + r} ${y}"/></g>`;
  }

  // Sinais de modo, desenhados por cima da criatura.
  const OVERLAY = `
    <g class="ov ov-dreaming"><text x="88" y="30">z</text><text x="98" y="18">z</text></g>
    <g class="ov ov-thinking"><circle cx="90" cy="28" r="2.6"/><circle cx="98" cy="22" r="3.2"/><circle cx="107" cy="14" r="4"/></g>
    <g class="ov ov-waiting"><circle cx="100" cy="22" r="11"/><text x="100" y="27" text-anchor="middle">!</text></g>
    <g class="ov ov-working" transform="translate(100 24)"><g class="gear">
      <circle r="7"/><g class="teeth"><rect x="-1.8" y="-11" width="3.6" height="5"/><rect x="-1.8" y="6" width="3.6" height="5"/>
      <rect x="-11" y="-1.8" width="5" height="3.6"/><rect x="6" y="-1.8" width="5" height="3.6"/></g><circle class="hole" r="2.6"/></g></g>`;

  function render(root, stage) {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 120 116");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = `<g class="creature">${body(stage)}</g>${OVERLAY}`;
    root.replaceChildren(svg);
  }

  /** Cria o avatar dentro de `root`. Devolve { setStage(n), setMode(nome) }. */
  function create(root) {
    root.classList.add("pet");
    let stage = -1;
    let mode = "idle";
    const api = {
      setStage(n) {
        const s = Math.max(0, Math.min(4, Number(n) || 0));
        if (s === stage) return;
        stage = s;
        root.dataset.stage = String(s);
        render(root, s);
      },
      setMode(m) {
        mode = MODES[m] ? m : "idle";
        root.dataset.mode = mode;
        root.title = `Agora: ${MODES[mode]} (estado real do sistema)`;
      },
      get mode() { return mode; },
      label: (m) => MODES[m] || MODES.idle,
    };
    api.setStage(0);
    api.setMode("idle");
    return api;
  }

  window.NymAvatar = { create, MODES };
})();
