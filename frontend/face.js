/* Rosto do Nym (Fase 10): olhos de robô desenhados em <canvas>, no espírito dos robozinhos de olhos em OLED.
 *
 * Implementação própria (não copia código de bibliotecas de terceiros). O que os olhos mostram NUNCA é sorteado no
 * sentido de "inventar um estado": o ESTADO vem do sistema real (respondendo, sonhando, esperando você, cansado...) e a
 * REAÇÃO vem da resposta (o próprio Nym escolhe com [[rosto: ...]]) ou da leitura da sua mensagem pelo código. Só os
 * detalhes de vida — piscar e o olhar passeando quando está à toa — são aleatórios, como em qualquer bichinho de tela.
 *
 * API: NymFace.create(canvas) -> { setState(nome), react(nome, ms), lookAt(x, y, ms), state, reaction, destroy() }
 */
(function () {
  // Forma de cada expressão. w/h: tamanho relativo do olho; r: arredondamento (0..0.5); top: pálpebra de cima (0..1)
  // com inclinação `slant` (+ = cantos de dentro mais baixos, cara brava; - = cantos de fora mais baixos, cara triste);
  // bottom: bochecha que sobe (olhos "^ ^" de feliz); dy: olhar para cima/baixo; left/right: escala de cada olho.
  const SHAPES = {
    neutro:       { w: 1, h: 1, r: 0.28 },
    feliz:        { w: 1.05, h: 1, r: 0.32, bottom: 0.48 },
    risada:       { w: 1.05, h: 0.95, r: 0.32, bottom: 0.55, anim: "bounce" },
    surpreso:     { w: 1.08, h: 1.28, r: 0.5 },
    irritado:     { w: 1.05, h: 0.95, r: 0.22, top: 0.42, slant: 0.5 },
    triste:       { w: 1, h: 0.9, r: 0.3, top: 0.3, slant: -0.45, dy: 0.12 },
    cansado:      { w: 1.05, h: 1, r: 0.25, top: 0.55 },
    curioso:      { w: 1, h: 1, r: 0.3, left: 1.12, right: 0.86, dy: -0.08 },
    confuso:      { w: 1, h: 1, r: 0.3, left: 0.82, right: 1.12, anim: "shake" },
    carinho:      { w: 1, h: 1, r: 0.3, heart: true, anim: "pulse" },
    assustado:    { w: 0.72, h: 0.78, r: 0.5, anim: "tremble", sweat: true },
    envergonhado: { w: 0.95, h: 0.8, r: 0.35, dy: 0.2, blush: true },
    // estados de base (o código decide)
    sonhando:     { w: 1.05, h: 0.07, r: 0.5, dy: 0.15, zz: true },
    dormindo:     { w: 1.05, h: 0.07, r: 0.5, dy: 0.15, zz: true },
    esperando:    { w: 1, h: 1.05, r: 0.32, ask: true },
    trabalhando:  { w: 1, h: 0.82, r: 0.25, scan: true },
    fome:         { w: 1.05, h: 1.18, r: 0.5, dy: -0.12, shine: true },
    pensando:     { w: 0.95, h: 0.95, r: 0.3, look: [0.45, -0.45] },
    falando:      { w: 1, h: 1, r: 0.3, anim: "talk" },
    fora:         { w: 1, h: 0.1, r: 0.5, dim: true, sweat: true },
  };
  SHAPES.cansado_estado = SHAPES.cansado;
  const BASE = { w: 1, h: 1, r: 0.28, top: 0, slant: 0, bottom: 0, dy: 0, left: 1, right: 1 };
  const NUM = ["w", "h", "r", "top", "slant", "bottom", "dy", "left", "right"];

  function css(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function roundRect(ctx, x, y, w, h, r) {
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function heart(ctx, cx, cy, s) {
    ctx.beginPath();
    ctx.moveTo(cx, cy + s * 0.35);
    ctx.bezierCurveTo(cx - s * 0.9, cy - s * 0.25, cx - s * 0.45, cy - s * 0.95, cx, cy - s * 0.4);
    ctx.bezierCurveTo(cx + s * 0.45, cy - s * 0.95, cx + s * 0.9, cy - s * 0.25, cx, cy + s * 0.35);
    ctx.closePath();
  }

  function create(canvas) {
    const ctx = canvas.getContext("2d");
    const off = document.createElement("canvas");              // olhos + pálpebras aqui; o brilho vem depois, no canvas real
    const octx = off.getContext("2d");
    let state = "neutro";
    let reaction = null, reactionUntil = 0;
    let look = [0, 0], lookTarget = [0, 0], lookUntil = 0, forcedLook = null;
    let cur = { ...BASE };
    let blinkAt = performance.now() + 1500, blinkT = -1;
    let wanderAt = 0, raf = 0, alive = true;
    const t0 = performance.now();

    function target() {
      const name = reaction && performance.now() < reactionUntil ? reaction : state;
      return { name, s: { ...BASE, ...(SHAPES[name] || SHAPES.neutro) } };
    }

    function step(now) {
      if (!alive) return;
      raf = requestAnimationFrame(step);
      if (document.hidden) return;
      const dpr = window.devicePixelRatio || 1;
      const W = canvas.clientWidth, H = canvas.clientHeight;
      if (!W || !H) return;
      if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
        canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      if (off.width !== canvas.width || off.height !== canvas.height) { off.width = canvas.width; off.height = canvas.height; }
      octx.setTransform(dpr, 0, 0, dpr, 0, 0);
      octx.clearRect(0, 0, W, H);

      const { name, s } = target();
      if (reaction && now >= reactionUntil) reaction = null;
      for (const k of NUM) cur[k] += (s[k] - cur[k]) * 0.18;          // transição suave entre expressões

      // olhar: forçado (você digitando), da expressão, ou passeando quando está à toa
      if (forcedLook && now < lookUntil) lookTarget = forcedLook;
      else if (s.look) lookTarget = s.look;
      else if (s.scan) lookTarget = [Math.sin((now - t0) / 700) * 0.55, 0.25];
      else if (name === "neutro" || name === "curioso") {
        if (now > wanderAt) {
          wanderAt = now + 1800 + Math.random() * 3200;
          lookTarget = Math.random() < 0.45 ? [0, 0] : [(Math.random() - 0.5) * 1.1, (Math.random() - 0.5) * 0.7];
        }
      } else lookTarget = [0, 0];
      look[0] += (lookTarget[0] - look[0]) * 0.12;
      look[1] += (lookTarget[1] - look[1]) * 0.12;

      // piscar (não pisca de olho fechado nem com corações)
      let blink = 1;
      const closed = s.h < 0.2 || s.heart;
      if (!closed && now > blinkAt) { blinkT = now; blinkAt = now + 2200 + Math.random() * 4200; }
      if (blinkT > 0) {
        const p = (now - blinkT) / 160;
        if (p >= 2) blinkT = -1; else blink = Math.max(0.06, Math.abs(1 - p));
      }

      // animações curtas
      const t = (now - t0) / 1000;
      let ox = 0, oy = 0, scale = 1;
      if (s.anim === "bounce") oy = -Math.abs(Math.sin(t * 9)) * H * 0.06;
      if (s.anim === "shake") ox = Math.sin(t * 18) * W * 0.02;
      if (s.anim === "tremble") { ox = (Math.random() - 0.5) * W * 0.012; oy = (Math.random() - 0.5) * H * 0.012; }
      if (s.anim === "pulse") scale = 1 + Math.sin(t * 6) * 0.06;
      if (s.anim === "talk") oy = Math.sin(t * 11) * H * 0.012;

      const accent = css("--accent", "#7ee0c8");
      const size = Math.min(W / 3.4, H / 1.8);
      const gap = size * 0.42;
      const cx = W / 2 + look[0] * W * 0.14 + ox;
      const cy = H / 2 + (look[1] + cur.dy) * H * 0.18 + oy;
      ctx.globalAlpha = s.dim ? 0.45 : 1;

      [-1, 1].forEach((side) => {
        const k = side < 0 ? cur.left : cur.right;
        const ew = size * cur.w * k * scale, eh = size * cur.h * k * scale * blink;
        const ex = cx + side * (gap / 2 + size * 0.5) - ew / 2;
        const ey = cy - eh / 2;
        const c = octx;
        c.save();
        if (s.heart) {
          heart(c, ex + ew / 2, cy, size * 0.62 * scale);
          c.fillStyle = css("--heart", "#ff6b9a");
          c.fill();
        } else {
          c.fillStyle = accent;
          roundRect(c, ex, ey, ew, eh, Math.min(ew, eh) * cur.r);
          c.fill();
          c.globalCompositeOperation = "destination-out";          // pálpebras recortam só o olho (o brilho vem depois)
          if (cur.top > 0.01) {
            const drop = eh * cur.top, tilt = eh * 0.35 * cur.slant;
            const inner = side < 0 ? ex + ew + 2 : ex - 2, outer = side < 0 ? ex - 2 : ex + ew + 2;   // de dentro = perto do outro olho
            c.beginPath();
            c.moveTo(outer, ey - 2);
            c.lineTo(inner, ey - 2);
            c.lineTo(inner, ey + drop + tilt);
            c.lineTo(outer, ey + drop - tilt);
            c.closePath();
            c.fill();
          }
          if (cur.bottom > 0.01) {                                  // bochecha subindo: olho vira "∩", o "^ ^" de feliz
            c.beginPath();
            c.ellipse(ex + ew / 2, ey + eh * 1.12, ew * 0.72, eh * cur.bottom * 1.25, 0, 0, Math.PI * 2);
            c.fill();
          }
          c.globalCompositeOperation = "source-over";
          if (s.shine) {                                            // brilho de olho pidão
            c.fillStyle = "rgba(255,255,255,.9)";
            c.beginPath(); c.arc(ex + ew * 0.32, ey + eh * 0.3, ew * 0.12, 0, Math.PI * 2); c.fill();
          }
        }
        if (s.blush) {
          c.fillStyle = "rgba(255,120,150,.45)";
          c.beginPath(); c.ellipse(ex + ew / 2, ey + eh + size * 0.2, ew * 0.38, size * 0.09, 0, 0, Math.PI * 2); c.fill();
        }
        c.restore();
      });
      ctx.save();
      ctx.shadowColor = s.heart ? css("--heart", "#ff6b9a") : accent;
      ctx.shadowBlur = size * 0.2;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.drawImage(off, 0, 0);
      ctx.restore();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      ctx.globalAlpha = 1;
      ctx.fillStyle = accent;
      ctx.font = `${Math.round(size * 0.32)}px system-ui, sans-serif`;
      if (s.zz) {                                                    // z z z subindo
        for (let i = 0; i < 3; i++) {
          const p = ((t * 0.45 + i / 3) % 1);
          ctx.globalAlpha = 1 - p;
          ctx.fillText("z", W / 2 + size * (0.7 + p * 0.6), H / 2 - size * (0.05 + p * 0.5));
        }
        ctx.globalAlpha = 1;
      }
      if (s.ask) {
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText("?", W / 2 + look[0] * W * 0.14, H / 2 + Math.sin(t * 3) * size * 0.05);
        ctx.textAlign = "start"; ctx.textBaseline = "alphabetic";
      }
      if (s.sweat) {
        const p = (t * 0.6) % 1;
        ctx.fillStyle = "#8fd3ff";
        ctx.globalAlpha = 1 - p * 0.7;
        ctx.beginPath(); ctx.ellipse(W / 2 + size * 1.2, H / 2 - size * 0.45 + p * size * 0.5, size * 0.07, size * 0.11, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
      }
    }

    raf = requestAnimationFrame(step);
    return {
      setState(name) { state = SHAPES[name] ? name : "neutro"; },
      react(name, ms = 6000) {
        if (!SHAPES[name]) return;
        reaction = name; reactionUntil = performance.now() + ms;
        blinkT = -1;
      },
      lookAt(x, y, ms = 1500) { forcedLook = [x, y]; lookUntil = performance.now() + ms; },
      get state() { return state; },
      get reaction() { return reaction && performance.now() < reactionUntil ? reaction : null; },
      destroy() { alive = false; cancelAnimationFrame(raf); },
    };
  }

  const LABELS = {
    feliz: "feliz", risada: "rindo", surpreso: "surpreso", irritado: "irritado", triste: "triste", curioso: "curioso",
    confuso: "confuso", carinho: "com carinho", assustado: "assustado", cansado: "cansado", envergonhado: "envergonhado",
    neutro: "à toa", sonhando: "sonhando", dormindo: "dormindo", esperando: "esperando você", trabalhando: "trabalhando",
    fome: "com fome", pensando: "pensando", falando: "falando", fora: "fora do ar",
  };
  window.NymFace = { create, SHAPES, LABELS };
})();
