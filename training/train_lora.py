"""Treina um LoRA do Nym em cima do Gemma 4 12B, na SUA placa de vídeo, com o Unsloth (QLoRA 4 bits).

ATENÇÃO: este script não roda dentro do Nym nem no venv dele. Ele precisa de PyTorch com CUDA, Unsloth e TRL,
que ficam num ambiente separado (veja training/LEIAME.md). Foi escrito a partir da documentação do Unsloth para o
Gemma 4 e AINDA NÃO FOI EXECUTADO numa GPU real: a primeira rodada é um teste.

Uso (no ambiente de treino):
    python training/train_lora.py --data data/training/nym_train.jsonl --eval data/training/nym_eval.jsonl

Saída (em data/training/lora-AAAAMMDD-HHMM/):
    adapter/     o LoRA em si (pequeno, dá para guardar várias versões)
    gguf/        modelo final (Gemma + LoRA mesclados) em GGUF q4_k_m, para o Ollama
    merged/      (só se o GGUF falhar) o mesmo modelo mesclado em 16 bits; o Ollama quantiza na instalação
    treino.json  configuração usada, perdas de treino/avaliação e tempo (o "diário" do treino)
Depois: python training/install_ollama.py data/training/lora-.../gguf (ou .../merged): cria "nym-gemma4" no Ollama

Se faltar memória na placa (erro "CUDA out of memory"): rode de novo com --max-len 1024 e/ou --rank 8.
Se mesmo assim não couber, o 12B não cabe em 12 GB para treinar e a alternativa é treinar o Gemma 4 E4B
(--base unsloth/gemma-4-E4B-it), mas aí o Nym passa a usar o E4B como cérebro.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Treina um LoRA do Nym (Unsloth + QLoRA).")
    p.add_argument("--data", default="data/training/nym_train.jsonl")
    p.add_argument("--eval", default="data/training/nym_eval.jsonl")
    p.add_argument("--base", default="unsloth/gemma-4-12b-it", help="modelo base no Hugging Face (o mesmo do Ollama)")
    p.add_argument("--out", default=None, help="pasta de saída (padrão: data/training/lora-<data>)")
    p.add_argument("--epochs", type=float, default=2.0)
    p.add_argument("--rank", type=int, default=16, help="tamanho do LoRA (8 = menor/mais leve, 32 = maior)")
    p.add_argument("--lr", type=float, default=5e-5)   # 1e-4 no 1º treino: picos de gradiente e JSON quebrado
    p.add_argument("--max-len", type=int, default=2048, help="tokens por exemplo (exemplos maiores são cortados)")
    p.add_argument("--gguf", default="q4_k_m", help="quantização do GGUF final ('' = não exporta)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    # Importa aqui: sem o ambiente de treino, --help ainda funciona e o erro fica claro.
    try:
        from unsloth import FastModel
        from unsloth.chat_templates import get_chat_template, train_on_responses_only
        from datasets import load_dataset
        from trl import SFTConfig, SFTTrainer
    except ImportError as exc:
        raise SystemExit(f"Falta o ambiente de treino ({exc}). Veja training/LEIAME.md.") from exc

    out = Path(args.out or f"data/training/lora-{time.strftime('%Y%m%d-%H%M')}")
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()

    model, tokenizer = FastModel.from_pretrained(model_name=args.base, max_seq_length=args.max_len,
                                                 load_in_4bit=True, full_finetuning=False)
    model = FastModel.get_peft_model(model, finetune_vision_layers=False, finetune_language_layers=True,
                                     finetune_attention_modules=True, finetune_mlp_modules=True,
                                     r=args.rank, lora_alpha=args.rank, lora_dropout=0, bias="none", random_state=3407)
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")   # sem modo de pensar, como o Nym usa

    def to_text(batch):
        texts = [tokenizer.apply_chat_template(m, tokenize=False, add_generation_prompt=False).removeprefix("<bos>")
                 for m in batch["messages"]]
        return {"text": texts}

    files = {"train": args.data}
    if args.eval and Path(args.eval).exists() and Path(args.eval).stat().st_size > 0:
        files["eval"] = args.eval
    data = load_dataset("json", data_files=files)
    train = data["train"].map(to_text, batched=True, remove_columns=data["train"].column_names)
    evals = data["eval"].map(to_text, batched=True, remove_columns=data["eval"].column_names) if "eval" in data else None

    trainer = SFTTrainer(
        model=model, tokenizer=tokenizer, train_dataset=train, eval_dataset=evals,
        args=SFTConfig(dataset_text_field="text", max_seq_length=args.max_len, per_device_train_batch_size=1,
                       gradient_accumulation_steps=8, warmup_steps=5, num_train_epochs=args.epochs,
                       learning_rate=args.lr, logging_steps=5, optim="adamw_8bit", weight_decay=0.01,
                       lr_scheduler_type="linear", seed=3407, max_grad_norm=0.3, output_dir=str(out / "checkpoints"),
                       eval_strategy="epoch" if evals is not None else "no", report_to="none"),
    )
    # Só as falas do Nym contam no aprendizado; as suas e o prompt são contexto.
    trainer = train_on_responses_only(trainer, instruction_part="<|turn>user\n", response_part="<|turn>model\n")
    stats = trainer.train()

    model.save_pretrained(str(out / "adapter"))
    tokenizer.save_pretrained(str(out / "adapter"))
    def write_log(exported: str) -> None:   # escrito ANTES da exportação também: se ela cair, o registro fica
        history = [h for h in trainer.state.log_history if "loss" in h or "eval_loss" in h]
        (out / "treino.json").write_text(json.dumps({
            "base": args.base, "data": args.data, "examples": len(train), "eval_examples": len(evals) if evals else 0,
            "epochs": args.epochs, "rank": args.rank, "lr": args.lr, "max_len": args.max_len, "gguf": args.gguf,
            "train_loss": getattr(stats, "training_loss", None), "history": history,
            "exported": exported, "minutes": round((time.time() - started) / 60, 1),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    write_log("exportando...")
    exported = "nenhum"
    if args.gguf:
        try:   # o Unsloth compila o llama.cpp para isso; no Windows pode faltar compilador
            model.save_pretrained_gguf(str(out / "gguf"), tokenizer, quantization_method=args.gguf)
            exported = "gguf" if list((out / "gguf").glob("*.gguf")) else "nenhum"
        except Exception as exc:  # noqa: BLE001 - qualquer falha aqui tem o mesmo plano B
            print(f"GGUF não saiu ({exc}).")
    if exported == "nenhum" and (out / "gguf" / "config.json").exists():
        exported = "gguf"   # a conversão parou, mas o modelo mesclado em 16 bits ficou salvo ali: o Ollama quantiza
    elif exported == "nenhum":
        print("Salvando o modelo mesclado para o próprio Ollama quantizar...")
        model.save_pretrained_merged(str(out / "merged"), tokenizer, save_method="merged_16bit")
        exported = "merged"
    write_log(exported)
    print(f"Pronto em {(time.time() - started) / 60:.0f} min. Resultado em {out}")
    print(f"Agora: .venv\\Scripts\\python.exe training/install_ollama.py {out / exported}")


if __name__ == "__main__":
    main()
