"""İkinci STT motoru: akışlı Zipformer transducer (sherpa-onnx), ör. mihuai/turkish-stt-66m-v2 (`STT_ENGINE=sherpa`).

Whisper motoruyla (engine.py) aynı arayüz: `transcribe(16 kHz float32 cümle sesi, ipuçları) → SttResult`. Servis
cümleyi VAD ile ayırıp verdiği için bu ilk sürümde model "cümle cümle" kullanılır; modelin asıl gücü olan
konuşurken yazma (kısmi sonuç) sonraki adımdır (tasarim.md 5.2.3).

Neden: GPU gerekmez (66 M parametre, int8 ~68 MB; CPU'da gerçek zamanın ~11 katı, ölçüm 02.11.2026) ve Whisper
gibi istem / ipucu devamı uydurmaz. Sınırlar:
  - Ad ipucu (bağlam yanlılığı) bu motorda kullanılmaz: ilk ölçümde çıktıyı değiştirmedi; Gateway'in ipuçları
    yok sayılır (bir kez loglanır). Ad doğruluğu diyalogdaki teyit sorusu ve bulanık aramayla korunur.
  - Güven: belirteç log-olasılıklarının ortalamasının üsteli (Whisper'ınkiyle aynı ölçekte değil: CONFIRM_BELOW
    bu motor için ayrıca ölçülüp ayarlanmalı).
  - LİSANS: mihu Community License — yıllık geliri 500.000 $ üstü kuruluşta ve ürünle dağıtımda ticari sözleşme
    gerekir (deploy/models.yaml: dagitilmaz). Model pakete girmez; kurulumda elle konur.
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from sesli_asistan.libs.text.normalizer import TrNormalizer

from .engine import SttResult

log = logging.getLogger(__name__)
SR = 16000
TAIL_S = 0.5            # sonda sessizlik: kod çözücü son kelimeleri de versin (model kartındaki değerlendirme gibi)
# başta sessizlik: VAD cümleyi konuşmanın hemen önünden verir; akışlı model ilk kelimeyi düşürüyordu
# (santral denemesi 02.11.2026: "görüşmeyi sonlandırmak istiyorum" → "sonlandırmak istiyorum"; 0,3 sn ile tam)
LEAD_S = 0.3


@dataclass(frozen=True)
class SherpaParams:
    model_dir: str = "models/turkish-stt-66m-v2"
    threads: int = 2
    num_workers: int = 2            # aynı anda çözülen cümle
    decoding: str = "modified_beam_search"
    max_active_paths: int = 4


class SherpaEngine:
    def __init__(self, params: SherpaParams | None = None, recognizer: Any = None):
        """recognizer: testler için sahte nesne verilebilir; verilmezse diskten yüklenir."""
        self.p = params or SherpaParams()
        self.normalizer = TrNormalizer(lowercase=True, strip_punct=True, latin_only=True)
        self.rec = recognizer if recognizer is not None else self._load()
        self.device = "cpu"
        self._slots = threading.Semaphore(max(1, self.p.num_workers))
        self._hint_warned = False

    def _load(self) -> Any:
        import sherpa_onnx

        d = Path(self.p.model_dir)
        files = {k: next(iter(sorted(d.glob(f"{k}*.onnx"))), None) for k in ("encoder", "decoder", "joiner")}
        missing = [k for k, v in files.items() if v is None] + ([] if (d / "tokens.txt").exists() else ["tokens.txt"])
        if missing:
            raise FileNotFoundError(f"{d}: {', '.join(missing)} yok: python tools/models/indir.py "
                                    "--model turkish-stt-66m-v2 (lisansı okuyun: deploy/models.yaml)")
        log.info("sherpa-onnx transducer yükleniyor: %s (%d iş parçacığı)", d, self.p.threads)
        return sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(d / "tokens.txt"), encoder=str(files["encoder"]), decoder=str(files["decoder"]),
            joiner=str(files["joiner"]), num_threads=self.p.threads, sample_rate=SR, feature_dim=80,
            decoding_method=self.p.decoding, max_active_paths=self.p.max_active_paths)

    def warmup(self) -> None:
        self.transcribe(np.zeros(SR, np.float32))

    def transcribe(self, samples: np.ndarray, hotwords: list[str] | None = None) -> SttResult:
        if samples.size == 0:
            return SttResult("", 0.0)
        if hotwords and not self._hint_warned:
            self._hint_warned = True
            log.info("sherpa motoru ad ipuçlarını kullanmıyor (%d ipucu yok sayıldı)", len(hotwords))
        x = np.ascontiguousarray(samples, dtype=np.float32)
        with self._slots:
            s = self.rec.create_stream()
            s.accept_waveform(SR, np.zeros(int(SR * LEAD_S), np.float32))
            s.accept_waveform(SR, x)
            s.accept_waveform(SR, np.zeros(int(SR * TAIL_S), np.float32))
            s.input_finished()
            while self.rec.is_ready(s):
                self.rec.decode_stream(s)
            raw = self.rec.get_result(s)
            text = raw if isinstance(raw, str) else getattr(raw, "text", "")
            try:
                probs = list(self.rec.ys_probs(s))
            except Exception:                          # eski sherpa-onnx: olasılık yok
                probs = []
        text = self.normalizer.normalize(text or "")
        # olasılık yoksa 0: Gateway güveni "bilinmiyor" sayar (teyit sorusu bu yüzden sorulmaz)
        conf = math.exp(sum(probs) / len(probs)) if probs else 0.0
        return SttResult(text, round(min(1.0, conf), 3))
