"""Instala no Ollama o modelo treinado (GGUF), usando o MESMO formato de conversa do modelo base.

Uso (só biblioteca padrão; pode ser o python do venv do Nym):
    .venv\\Scripts\\python.exe training/install_ollama.py data/training/lora-AAAAMMDD-HHMM/gguf

Pega o Modelfile do modelo base (`ollama show <base> --modelfile`), troca só a linha FROM pelo GGUF novo e cria o
modelo com `ollama create`. Não apaga nem muda o modelo base: o Nym continua com o Gemma até você trocar NYM_MODEL.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


BIG_GGUF = 12 * 1024 ** 3


def find_source(folder: Path) -> tuple[Path, bool]:
    """(o que vai no FROM, precisa quantizar?). GGUF pronto, ou pasta mesclada em safetensors (o Ollama quantiza)."""
    if folder.is_dir() and (folder / "config.json").exists() and not list(folder.glob("*.gguf")):
        return folder, True
    gguf = find_gguf(folder)
    return gguf, gguf.stat().st_size > BIG_GGUF   # GGUF em 16 bits (~24 GB) não cabe na placa: o Ollama quantiza


def find_gguf(folder: Path) -> Path:
    files = sorted(folder.glob("*.gguf"), key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        raise SystemExit(f"Nenhum .gguf em {folder}.")
    return files[0]   # o maior é o modelo; arquivos pequenos costumam ser projeções de visão/áudio


def modelfile_from(base_modelfile: str, gguf: Path) -> str:
    """Troca as linhas FROM (o modelo base) pelo GGUF novo, mantendo TEMPLATE, PARAMETER e o resto."""
    lines, replaced = [], False
    for line in base_modelfile.splitlines():
        if line.strip().upper().startswith("FROM "):
            if not replaced:
                lines.append(f"FROM {gguf.resolve()}")
                replaced = True
            continue
        lines.append(line)
    if not replaced:
        lines.insert(0, f"FROM {gguf.resolve()}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Cria o modelo do Nym treinado no Ollama.")
    p.add_argument("gguf_dir", help="pasta gguf/ (ou merged/) gerada pelo train_lora.py")
    p.add_argument("--base", default="gemma4:12b-it-qat", help="modelo base já instalado no Ollama")
    p.add_argument("--name", default="nym-gemma4", help="nome do modelo novo no Ollama")
    p.add_argument("--quantize", default=None, help="tipo de compressão (padrão: int4 para safetensors, q4_K_M para GGUF)")
    args = p.parse_args(argv)
    gguf, quantize = find_source(Path(args.gguf_dir))
    shown = subprocess.run(["ollama", "show", args.base, "--modelfile"], capture_output=True, text=True,
                           encoding="utf-8", check=False)
    if shown.returncode != 0:
        raise SystemExit(f"Não consegui ler o modelo base {args.base}: {shown.stderr.strip()}")
    target = Path(args.gguf_dir) / "Modelfile"
    target.write_text(modelfile_from(shown.stdout, gguf), encoding="utf-8")
    # Safetensors: o Ollama novo só aceita int4/int8/... (visto no uso real); GGUF grande: q4_K_M.
    kind = args.quantize or ("int4" if gguf.is_dir() else "q4_K_M")
    cmd = ["ollama", "create", args.name, "-f", str(target)] + (["--quantize", kind] if quantize else [])
    created = subprocess.run(cmd, check=False)
    if created.returncode != 0:
        return created.returncode
    print(f"Modelo '{args.name}' criado. Compare antes de trocar:\n"
          f"  .venv\\Scripts\\python.exe -m app.brain_check {args.name} {args.base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
