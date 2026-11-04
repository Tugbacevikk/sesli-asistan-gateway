"""Santral dil modeli (LSTM) eğitimi ve sherpa-onnx için ONNX'e aktarma (ADR-028).

mihu'nun akustik modeline dokunulmaz: dil modeli ayrı bir küçük ağdır, çözümlemede akustik skora `lm_scale` ile
eklenir (shallow fusion). Model "sahil" ile "sipariş" arasında kararsız kaldığında santral cümlesini seçmesine yardım
eder; ses gerçekten kayıpsa düzeltemez.

    pip install torch sentencepiece onnx onnxruntime
    python tools/stt/dil_modeli/derlem.py --rehber data/rehber_ornek.json --cikti derlem.txt
    python tools/stt/dil_modeli/egit.py derlem.txt --model-dizini models/turkish-stt-66m-v2 \
        --cikti models/santral_lm.onnx

Belirteçler mihu'nun kendi BPE modeliyle (`bpe.model`) üretilir; kimlikler `tokens.txt` ile denetlenir (uyuşmazsa
durur: yanlış kimlikle eğitilen dil modeli çözümlemeyi bozar). sherpa-onnx sözleşmesi (online-rnn-lm.cc):
  girdi  x [1, T] int64, h [katman, 1, gizli], c [katman, 1, gizli]
  çıktı  log-olasılık [1, T, sözlük], yeni h, yeni c
  meta   num_layers, hidden_size, sos_id (+ eos_id, vocab_size, model_type=rnnlm)
CPU'da 60 bin cümle, 2 × 512 LSTM: birkaç dakika / tur.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import time
from pathlib import Path


def read_tokens(path: Path) -> dict[str, int]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            piece, idx = line.rsplit(maxsplit=1)
            out[piece] = int(idx)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("derlem", type=Path, help="satır başına bir cümle (derlem.py)")
    ap.add_argument("--model-dizini", type=Path, required=True, help="bpe.model ve tokens.txt'nin olduğu klasör")
    ap.add_argument("--cikti", type=Path, required=True, help="ONNX dil modeli")
    ap.add_argument("--katman", type=int, default=2)
    ap.add_argument("--gizli", type=int, default=512)
    ap.add_argument("--tur", type=int, default=4, help="eğitim turu (epoch)")
    ap.add_argument("--yigin", type=int, default=64, help="yığın (batch) boyu")
    ap.add_argument("--oran", type=float, default=1e-3, help="öğrenme oranı")
    ap.add_argument("--tohum", type=int, default=7)
    a = ap.parse_args(argv)

    import sentencepiece as spm
    import torch
    from torch import nn

    torch.manual_seed(a.tohum)
    random.seed(a.tohum)
    sp = spm.SentencePieceProcessor(model_file=str(a.model_dizini / "bpe.model"))
    tokens = read_tokens(a.model_dizini / "tokens.txt")
    vocab = max(tokens.values()) + 1
    # BPE kimlikleri tokens.txt ile aynı olmalı (akustik modelin çıktı kimlikleri)
    bad = [p for p, i in tokens.items() if 0 <= i < sp.get_piece_size() and sp.id_to_piece(i) != p
           and p not in ("<blk>", "<sos/eos>", "<unk>")]
    if bad:
        print(f"HATA: bpe.model ile tokens.txt uyuşmuyor ({len(bad)} parça, ör. {bad[:3]})", file=sys.stderr)
        return 1
    sos = tokens.get("<sos/eos>", 1)
    eos = sos

    lines = [x.strip() for x in a.derlem.read_text(encoding="utf-8").splitlines() if x.strip()]
    seqs = [ids for ids in (sp.encode(x, out_type=int) for x in lines) if 0 < len(ids) <= 60]
    random.shuffle(seqs)
    n_val = max(200, len(seqs) // 50)
    val, train = seqs[:n_val], seqs[n_val:]
    print(f"derlem: {len(seqs)} cümle (eğitim {len(train)}, doğrulama {len(val)}), sözlük {vocab}, sos/eos {sos}")

    class Lm(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.emb = nn.Embedding(vocab, a.gizli)
            self.rnn = nn.LSTM(a.gizli, a.gizli, a.katman, batch_first=True)
            self.out = nn.Linear(a.gizli, vocab)

        def forward(self, x, h=None, c=None):
            y, (h, c) = self.rnn(self.emb(x), None if h is None else (h, c))
            return self.out(y), h, c

    model = Lm()
    opt = torch.optim.Adam(model.parameters(), lr=a.oran)
    loss_fn = nn.CrossEntropyLoss(ignore_index=-1, reduction="sum")

    def batches(data, shuffle):
        idx = list(range(len(data)))
        if shuffle:
            random.shuffle(idx)
        for i in range(0, len(idx), a.yigin):
            group = [data[j] for j in idx[i:i + a.yigin]]
            t = max(len(s) for s in group) + 1
            x = torch.full((len(group), t), eos, dtype=torch.long)
            y = torch.full((len(group), t), -1, dtype=torch.long)
            for k, s in enumerate(group):
                x[k, :len(s) + 1] = torch.tensor([sos, *s])
                y[k, :len(s) + 1] = torch.tensor([*s, eos])
            yield x, y

    def evaluate() -> float:
        model.eval()
        total, count = 0.0, 0
        with torch.no_grad():
            for x, y in batches(val, False):
                logits, _, _ = model(x)
                total += loss_fn(logits.reshape(-1, vocab), y.reshape(-1)).item()
                count += int((y >= 0).sum())
        model.train()
        return math.exp(total / max(count, 1))

    print(f"başlangıç doğrulama şaşkınlığı (perplexity): {evaluate():.1f}")
    for ep in range(1, a.tur + 1):
        t0 = time.time()
        for x, y in batches(train, True):
            logits, _, _ = model(x)
            loss = loss_fn(logits.reshape(-1, vocab), y.reshape(-1)) / max(int((y >= 0).sum()), 1)
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
        print(f"tur {ep}: doğrulama şaşkınlığı {evaluate():.2f} ({time.time() - t0:.0f} sn)")

    export(model, a, vocab, sos, eos)
    check(a.cikti, a.katman, a.gizli, vocab)
    return 0


def export(model, a, vocab: int, sos: int, eos: int) -> None:
    import onnx
    import torch

    class Step(torch.nn.Module):
        def __init__(self, m) -> None:
            super().__init__()
            self.m = m

        def forward(self, x, h, c):
            logits, h2, c2 = self.m(x, h, c)
            return torch.log_softmax(logits, dim=-1), h2, c2

    model.eval()
    x = torch.tensor([[sos]], dtype=torch.long)
    h = torch.zeros(a.katman, 1, a.gizli)
    c = torch.zeros(a.katman, 1, a.gizli)
    a.cikti.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(Step(model), (x, h, c), str(a.cikti), opset_version=13, dynamo=False,
                      input_names=["x", "h0", "c0"], output_names=["log_prob", "next_h0", "next_c0"],
                      dynamic_axes={"x": {1: "T"}, "log_prob": {1: "T"}})
    m = onnx.load(str(a.cikti))
    meta = {"model_type": "rnnlm", "version": "1", "vocab_size": str(vocab), "num_layers": str(a.katman),
            "hidden_size": str(a.gizli), "sos_id": str(sos), "eos_id": str(eos),
            "comment": "Sesli asistan santral dil modeli (ADR-028); mihu BPE belirteçleri"}
    for k, v in meta.items():
        p = m.metadata_props.add()
        p.key, p.value = k, v
    onnx.save(m, str(a.cikti))
    print(f"yazıldı: {a.cikti} ({a.cikti.stat().st_size / 1e6:.1f} MB)")


def check(path: Path, layers: int, hidden: int, vocab: int) -> None:
    """sherpa-onnx'in çağıracağı biçimde bir adım: şekiller ve olasılık toplamı."""
    import numpy as np
    import onnxruntime as ort

    s = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    meta = s.get_modelmeta().custom_metadata_map
    out = s.run(None, {"x": np.array([[1]], np.int64), "h0": np.zeros((layers, 1, hidden), np.float32),
                       "c0": np.zeros((layers, 1, hidden), np.float32)})
    assert out[0].shape == (1, 1, vocab) and out[1].shape == (layers, 1, hidden), [o.shape for o in out]
    assert abs(float(np.exp(out[0]).sum()) - 1) < 1e-3
    print(f"denetim tamam: meta {sorted(meta)}")


if __name__ == "__main__":
    raise SystemExit(main())
