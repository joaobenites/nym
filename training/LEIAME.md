# LoRA do Nym: treinar o jeito dele na sua placa

O Nym continua usando o Gemma 4 como cérebro. O LoRA é um "ajuste fino" pequeno por cima dele, treinado com as
conversas e o diário REAIS do Nym. Depois de mesclado, vira um modelo novo no Ollama (`nym-gemma4`), e o Gemma
original continua lá. Para voltar atrás, basta trocar `NYM_MODEL` no `.env`.

**Situação:** a exportação dos dados foi testada. O treino (`train_lora.py`) foi escrito pela documentação do
Unsloth, mas **ainda não rodou numa GPU de verdade**, então a primeira vez é um teste. Treinar o Gemma 4 12B em
12 GB fica no limite: se faltar memória, os passos 4 e 5 dizem o que fazer.

## 1. Exportar os dados (no venv do Nym, leva segundos)
```powershell
.venv\Scripts\python.exe -m app.training.export
```
Os arquivos vão para `data/training/` (que fica fora do Git), junto com o `relatorio.txt`. Ficam de fora as respostas
com vício de voz, as que você marcou com 👎 e as que têm cara de dado sensível. As marcadas com 👍 entram em dobro.
Entram também os exemplos de `training/sintetico.jsonl` (69, escritos à mão pelo Claude a seu pedido em 19/10): conversas
curtas no jeito do Nym, honestidade com a memória ("não tenho isso registrado"), pedidos de tarefa com a marca
`[[fazer: ...]]`, páginas de diário e planos em JSON. Eles **não entram no banco**, então nunca viram lembrança. Você
pode abrir, editar ou apagar linhas desse arquivo. Para treinar sem eles, use `--sem-sintetico`.
**Com menos de ~150 exemplos reais, o LoRA aprende só o tom.** Reagir às respostas no chat por alguns dias melhora muito
o resultado.

## 2. Montar o ambiente de treino (uma vez só; é separado do Nym)
Ele é pesado (PyTorch com CUDA, uns 6 GB). Fica numa pasta própria e não mexe no `.venv` do Nym.
```powershell
nvidia-smi                                   # veja o "CUDA Version" no canto superior direito
py -3.12 -m venv .venv-train
.venv-train\Scripts\python.exe -m pip install --upgrade pip
# troque cu128 pela versão do seu nvidia-smi que exista em pytorch.org (ex.: cu126, cu128, cu130)
.venv-train\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
.venv-train\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"   # tem que dar True
.venv-train\Scripts\python.exe -m pip install unsloth
```
Se a instalação nativa no Windows der problema, o caminho que a documentação do Unsloth indica como alternativa é o
WSL (Ubuntu). A pasta do projeto aparece lá em `/mnt/c/Users/Home/Desktop/nym`.

## 3. Treinar (feche o Nym e o Ollama antes, para liberar a placa)
```powershell
.venv-train\Scripts\python.exe training/train_lora.py
```
Na primeira vez ele baixa o Gemma 4 12B do Hugging Face (alguns GB). Com ~150 exemplos e 2 épocas, o treino deve
levar minutos, não horas. Isso é uma estimativa: o tempo real vai para `treino.json`.

## 4. Se faltar memória ("CUDA out of memory")
```powershell
.venv-train\Scripts\python.exe training/train_lora.py --max-len 1024 --rank 8
```
Se mesmo assim não couber, o 12B não dá para treinar em 12 GB. A alternativa é o Gemma 4 E4B
(`--base unsloth/gemma-4-E4B-it`), que é menor, mas aí ele vira o cérebro do Nym. Antes, compare os dois com o teste de
cérebro.

## 4b. Converter para o Ollama (no Windows, foi assim que funcionou em 19/10)
O Unsloth não conseguiu gerar o GGUF, e o Ollama no Windows não comprime a partir de safetensors (falta o MLX). O
caminho que funcionou foi converter com o llama.cpp. O código fica em `llama.cpp-master`, e o `llama-quantize.exe`
pronto vem na pasta `llama-bin` (zip `win-cpu-x64` das releases). Precisa de ~35 GB livres no disco durante a conversão.
```powershell
.\.venv-train\Scripts\python.exe -m pip install .\llama.cpp-master\gguf-py      # uma vez só
$L = "data\training\lora-AAAAMMDD-HHMM"
mkdir $L\final
.\.venv-train\Scripts\python.exe llama.cpp-master\convert_hf_to_gguf.py $L\gguf --outtype bf16 --outfile $L\nym-bf16.gguf
.\llama-bin\llama-quantize.exe $L\nym-bf16.gguf $L\final\nym-Q4_K_M.gguf Q4_K_M
Remove-Item $L\nym-bf16.gguf; Remove-Item $L\gguf\model.safetensors            # libera ~48 GB
```
Depois use a pasta `final` no passo 5.

## 5. Instalar no Ollama e comparar
```powershell
.venv\Scripts\python.exe training/install_ollama.py data/training/lora-AAAAMMDD-HHMM/final
.venv\Scripts\python.exe -m app.brain_check nym-gemma4 gemma4:12b-it-qat
```
Se o GGUF não tiver saído (o Unsloth precisa compilar o llama.cpp, e no Windows pode faltar compilador), o script
salva a pasta `merged/` no lugar. Use essa pasta no comando acima, e o próprio Ollama quantiza.
Se o `nym-gemma4` ganhar no teste de cérebro, ponha `NYM_MODEL=nym-gemma4` no `.env`. O modelo treinado só tem texto,
então para a visão continuar funcionando, ponha também `NYM_VISION_MODEL=gemma4:12b-it-qat`.

## O que o 1º treino ensinou (19/10)
O tom ficou mais solto ("E aí, João! Apareceu."), mas apareceram três problemas: o JSON do plano quebrou, o diário
citou um "Claudio" e escreveu "fiquei curiosa". A causa estava nos dados: tinha conversa de teste de outra IA
("aqui é o Claudio") e não havia nenhum exemplo de JSON. A exportação agora tira essas conversas e as respostas
que afirmam "ele me disse..." sem base, e inclui os planos reais que deram certo. O treino agora usa taxa de
aprendizado menor (5e-5) e limita o tamanho dos ajustes (max_grad_norm 0.3).

## O que isso é e o que não é
- **É:** uma mudança real nos pesos do modelo (um LoRA mesclado), feita por você, com dados que você pode abrir e
  conferir (`nym_train.jsonl`), e com o registro do treino (`treino.json`).
- **Não é:** o Nym aprendendo sozinho. Ele não treina a si mesmo, e cada treino é você que roda. As lições e os gostos
  do dia a dia continuam no banco, como sempre.
- O prompt de treino é curto, mas no uso real o Nym recebe a identidade completa. O LoRA ensina o jeito de falar; a
  identidade e as memórias continuam vindo do banco.
