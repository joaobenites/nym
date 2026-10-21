"""Validação do que o Nym cria (Fase 7). O modelo escreve; este código decide se aquilo pode ser guardado e mostrado.

- Desenho (SVG): reconstruído do zero só com elementos e atributos permitidos. Sai tudo o que executa código ou
  busca algo fora (script, foreignObject, on*, links externos, url() externo, <style>, DOCTYPE/ENTITY). A interface
  ainda mostra o desenho como imagem (<img>), onde SVG nunca executa nada: são duas travas.
- Brinquedo (HTML/JS): o Nym pode programar coisinhas interativas. Elas só rodam no navegador, dentro de um iframe
  isolado (sandbox sem mesma origem) com política que bloqueia qualquer acesso à rede. Aqui recusamos o que tenta
  carregar algo de fora, para o erro aparecer na hora e não no navegador.
- Código (Python): guardado para LER. O Nym nunca executa o que escreve. Precisa ser Python válido (ast.parse,
  que só analisa o texto) e recebe avisos visíveis quando usa coisas sensíveis (subprocess, rede, apagar arquivos).
- Texto: sem placeholder e com conteúdo de verdade.
"""
from __future__ import annotations

import ast
import re
import xml.etree.ElementTree as ET

from app.tools.base import ToolError

SVG_NS = "http://www.w3.org/2000/svg"
MAX_SVG_CHARS = 200_000
MAX_SVG_ELEMENTS = 3000
MAX_TOY_CHARS = 120_000
MAX_CODE_CHARS = 60_000
MAX_TEXT_CHARS = 20_000

SVG_ELEMENTS = {
    "svg", "g", "defs", "rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text", "tspan",
    "lineargradient", "radialgradient", "stop", "title", "desc", "use", "clippath", "mask", "pattern", "symbol",
    "filter", "fegaussianblur", "feoffset", "feblend", "fecolormatrix", "femerge", "femergenode", "feflood",
    "fecomposite", "feturbulence", "fedisplacementmap", "animate", "animatetransform", "animatemotion",
}
# Nomes com maiúscula do SVG (o XML diferencia): devolvemos a grafia certa ao reconstruir.
CANONICAL = {n.lower(): n for n in (
    "linearGradient", "radialGradient", "clipPath", "feGaussianBlur", "feOffset", "feBlend", "feColorMatrix",
    "feMerge", "feMergeNode", "feFlood", "feComposite", "feTurbulence", "feDisplacementMap", "animateTransform",
    "animateMotion")}
SVG_ATTRS = {
    "id", "class", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "fx", "fy", "width", "height",
    "d", "points", "viewbox", "preserveaspectratio", "transform", "fill", "fill-opacity", "fill-rule", "stroke",
    "stroke-width", "stroke-opacity", "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "stroke-dashoffset",
    "stroke-miterlimit", "opacity", "style", "font-size", "font-family", "font-weight", "font-style", "text-anchor",
    "dominant-baseline", "letter-spacing", "dx", "dy", "rotate", "offset", "stop-color", "stop-opacity",
    "gradientunits", "gradienttransform", "spreadmethod", "patternunits", "patterntransform", "clip-path",
    "clippathunits", "mask", "maskunits", "filter", "stddeviation", "in", "in2", "result", "mode", "values",
    "type", "operator", "k1", "k2", "k3", "k4", "flood-color", "flood-opacity", "basefrequency", "numoctaves",
    "seed", "scale", "xchannelselector", "ychannelselector", "attributename", "from", "to", "by", "dur", "begin",
    "end", "repeatcount", "keytimes", "keysplines", "calcmode", "additive", "accumulate", "path", "visibility",
    "display", "href", "xlink:href", "version", "xmlns", "xmlns:xlink",
}
_EXTERNAL_URL = re.compile(r"url\(\s*['\"]?\s*(?!#)", re.I)


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    m = re.match(r"^```[\w-]*\s*\n(.*?)\n?```\s*$", text, re.S)
    return m.group(1).strip() if m else text


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr_name(name: str) -> str:
    if name.startswith("{"):
        ns, local = name[1:].split("}", 1)
        return f"xlink:{local}" if ns == "http://www.w3.org/1999/xlink" else local
    return name


def sanitize_svg(raw: str) -> str:
    text = _strip_fences(raw)
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.I):
        raise ToolError("Desenho recusado: o SVG não pode ter DOCTYPE/ENTITY.")
    start, end = text.lower().find("<svg"), text.lower().rfind("</svg>")
    if start < 0 or end < 0:
        raise ToolError("Não saiu um desenho: faltou o <svg>...</svg>.", retryable=True)
    text = text[start:end + 6]
    if len(text) > MAX_SVG_CHARS:
        raise ToolError("Desenho grande demais.", retryable=True)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ToolError(f"O SVG saiu com erro de formato ({exc}).", retryable=True) from None

    count = 0
    drawn = 0

    def clean(el: ET.Element) -> ET.Element | None:
        nonlocal count, drawn
        name = _local(el.tag).lower()
        if name not in SVG_ELEMENTS:
            return None                                 # script, foreignObject, style, image, a... saem inteiros
        count += 1
        if count > MAX_SVG_ELEMENTS:
            raise ToolError("Desenho com elementos demais.", retryable=True)
        if name in ("rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text"):
            drawn += 1
        out = ET.Element(CANONICAL.get(name, name))
        for key, value in el.attrib.items():
            attr = _attr_name(key)
            low = attr.lower()
            if low.startswith("on") or low not in SVG_ATTRS or low in ("xmlns", "xmlns:xlink"):
                continue
            value = str(value)
            if low in ("href", "xlink:href") and not value.strip().startswith("#"):
                continue                                 # só referência interna (#id)
            if low == "attributename" and "href" in value.lower():
                continue                                 # animação que troca link: vetor clássico de ataque
            if _EXTERNAL_URL.search(value) or "javascript:" in value.lower() or "expression(" in value.lower():
                continue
            out.set(attr, value)
        out.text = el.text if name in ("text", "tspan", "title", "desc") else None
        for child in el:
            c = clean(child)
            if c is not None:
                c.tail = child.tail if name in ("text", "tspan") else None
                out.append(c)
        return out

    if _local(root.tag).lower() != "svg":
        raise ToolError("O desenho precisa começar com <svg>.", retryable=True)
    svg = clean(root)
    if drawn < 3:
        raise ToolError("O desenho saiu vazio demais (menos de 3 formas).", retryable=True)
    if not svg.get("viewBox") and not svg.get("viewbox"):
        w, h = svg.get("width", "400"), svg.get("height", "400")
        try:
            svg.set("viewBox", f"0 0 {float(str(w).rstrip('px'))} {float(str(h).rstrip('px'))}")
        except ValueError:
            svg.set("viewBox", "0 0 400 400")
    svg.set("xmlns", SVG_NS)
    return ET.tostring(svg, encoding="unicode")


_TOY_EXTERNAL = re.compile(
    r"(?:\b(?:src|href|action|data)\s*=\s*['\"]?\s*(?:https?:|//|file:))|url\(\s*['\"]?\s*(?:https?:|//)|@import|"
    r"\bfetch\s*\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|\bimportScripts\b|\bimport\s*\(|"
    r"document\.cookie|localStorage|sessionStorage|indexedDB|window\.open|\btop\.location|parent\.",
    re.I)


def check_toy(raw: str) -> str:
    text = _strip_fences(raw)
    if len(text) > MAX_TOY_CHARS:
        raise ToolError("Brinquedo grande demais.", retryable=True)
    if "<script" not in text.lower():
        raise ToolError("Um brinquedo precisa de interação: faltou <script>.", retryable=True)
    m = _TOY_EXTERNAL.search(text)
    if m:
        raise ToolError(f"O brinquedo tenta usar algo de fora da caixa dele ({m.group(0)[:40]}); só pode ser autocontido.",
                        retryable=True)
    return text


RISKY_CODE = {
    "subprocess": "roda programas do sistema", "os.system": "roda comandos do sistema", "eval(": "executa texto como código",
    "exec(": "executa texto como código", "__import__": "importa módulos dinamicamente", "socket": "usa a rede",
    "requests": "usa a rede", "urllib": "usa a rede", "shutil.rmtree": "apaga pastas", "os.remove": "apaga arquivos",
    "unlink(": "apaga arquivos", "ctypes": "acessa a memória do sistema", "pickle": "carrega dados executáveis",
}


def code_warnings(code: str) -> list[str]:
    return [f"{k}: {v}" for k, v in RISKY_CODE.items() if k in code]


def check_code(raw: str) -> str:
    text = _strip_fences(raw)
    if len(text) > MAX_CODE_CHARS:
        raise ToolError("Código grande demais.", retryable=True)
    if len(text.strip()) < 20:
        raise ToolError("Código vazio.", retryable=True)
    try:
        ast.parse(text)            # só analisa o texto; nada é executado
    except SyntaxError as exc:
        raise ToolError(f"O código não é Python válido (linha {exc.lineno}: {exc.msg}).", retryable=True) from None
    return text


def check_text(raw: str) -> str:
    from app.tools.builtin import looks_unfinished  # import local: evita ciclo
    text = _strip_fences(raw)
    if len(text) < 40:
        raise ToolError("Texto curto demais para ser uma criação.", retryable=True)
    if looks_unfinished(text):
        raise ToolError("O texto saiu com partes para preencher.", retryable=True)
    return text[:MAX_TEXT_CHARS]


CHECKS = {"drawing": sanitize_svg, "toy": check_toy, "code": check_code, "text": check_text}
