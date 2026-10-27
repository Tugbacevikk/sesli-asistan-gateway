"""Seslendirme: metni cümlelere böl, TTS'e ver, telefon sesine çevir, arayana çal (tasarim.md 5.4, 7.2, 9.1).

Gecikme için (NFR-01):
  - Cevap **cümle cümle** seslendirilir; ilk cümle hazır olunca çalmaya başlar.
  - Boru hattı: n. cümle çalınırken (n+1). cümle arka planda seslendirilir.
  - Sabit anonslar (karşılama, operatör, hata…) açılışta bir kez seslendirilip saklanır (`PromptLibrary`);
    aynı metin istenince TTS'e hiç gidilmez. TTS çökse bile bu anonslar çalınabilir.
Barge-in (NFR-02): `say()` görevi iptal edilirse bekleyen seslendirme de iptal edilir, çalma o pakette durur.
Hata (9.1): TTS yetişmez ya da hata verirse, henüz hiçbir şey çalınmadıysa yedek anons çalınır ve sonuç
`ok=False` döner; operatöre aktarma kararı çağrı oturumundadır (Aşama 5).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

import numpy as np

from sesli_asistan.libs.audio.resample import resample

from .ports import Audio, AudioOutPort, TtsError, TtsPort
from .search import number_to_words

log = logging.getLogger(__name__)

LINE_RATE = 8000

# --- metin hazırlama -------------------------------------------------------------------------

# Nokta ile biten ama cümle bitirmeyen kısaltmalar
_ABBREV = {"dr", "av", "prof", "doç", "uzm", "op", "müh", "öğr", "gör", "vb", "vs", "örn", "no", "tel", "sn", "bkz"}
_SENT_END = re.compile(r"([.!?…]+)(\s+|$)")
_SEPARATED_DIGITS = re.compile(r"\b\d(?:[-–]\d)+\b")    # "2-1-4" → tek tek
_DECIMAL = re.compile(r"\b(\d+),(\d+)\b")              # "3,5" → "üç virgül beş"
_PERCENT = re.compile(r"%\s?(\d+)")
_NUMBER = re.compile(r"\b\d{1,6}\b")
_DROP = re.compile(r"[*_#`>|\[\]{}<>\"“”]+")


def prepare_for_tts(text: str) -> str:
    """Seslendirmeye uygun metin: rakamlar Türkçe okunuşa çevrilir, işaretler atılır.

    TTS'in rakamları kendi okumasına bırakılmaz; okunuş sesler arasında farklı olabilir ve test edilemez."""
    t = _DROP.sub(" ", text or "")
    t = _SEPARATED_DIGITS.sub(lambda m: " ".join(number_to_words(int(d)) for d in re.findall(r"\d", m.group())), t)
    t = _PERCENT.sub(lambda m: f"yüzde {number_to_words(int(m.group(1)))}", t)
    t = _DECIMAL.sub(lambda m: f"{number_to_words(int(m.group(1)))} virgül {number_to_words(int(m.group(2)))}", t)
    t = _NUMBER.sub(lambda m: number_to_words(int(m.group())), t)
    return " ".join(t.split())


def split_sentences(text: str, max_chars: int = 160) -> list[str]:
    """Türkçe cümle bölme. Kısaltmalarda ("Dr.") ve ondalıkta bölmez; çok uzun cümleyi virgülden böler."""
    out, start = [], 0
    for m in _SENT_END.finditer(text):
        word = text[:m.start()].split()[-1:] or [""]
        if m.group(1) == "." and word[0].lower().rstrip(".") in _ABBREV:
            continue
        out.append(text[start:m.end()].strip())
        start = m.end()
    if text[start:].strip():
        out.append(text[start:].strip())
    result = []
    for s in out:
        while len(s) > max_chars and "," in s[:max_chars]:
            cut = s.rindex(",", 0, max_chars) + 1
            result.append(s[:cut].strip())
            s = s[cut:].strip()
        if s:
            result.append(s)
    return result


# --- telefon sesi ----------------------------------------------------------------------------

def to_line(audio: Audio, peak: float = 0.89) -> np.ndarray:
    """TTS sesi → 8 kHz int16; tepe değeri `peak`'i (≈ -1 dBFS) aşıyorsa kısılır (G.711'de kırpılma olmasın)."""
    x = resample(audio.pcm.astype(np.float32) / 32768.0, audio.rate, LINE_RATE)
    m = float(np.max(np.abs(x))) if len(x) else 0.0
    if m > peak:
        x = x * (peak / m)
    return np.clip(np.round(x * 32767), -32768, 32767).astype(np.int16)


# --- sabit anonslar --------------------------------------------------------------------------

class PromptLibrary:
    """Sabit anonsların hazır telefon sesi. Anahtar: metnin kendisi (normalize)."""

    def __init__(self):
        self._items: dict[str, np.ndarray] = {}
        self._keys: dict[str, str] = {}

    @staticmethod
    def _norm(text: str) -> str:
        return " ".join((text or "").split()).casefold()

    async def preload(self, tts: TtsPort, prompts: dict[str, str]) -> list[str]:
        """prompts: ad → metin. Seslendirilemeyenlerin adlarını döner (servis "hazır değil" sayılabilir)."""
        missing = []
        for name, text in prompts.items():
            try:
                pcm = np.concatenate([to_line(await tts.synthesize(prepare_for_tts(s))) for s in split_sentences(text)])
            except TtsError as e:
                log.warning("anons seslendirilemedi (%s): %s", name, e)
                missing.append(name)
                continue
            self._items[self._norm(text)] = pcm
            self._keys[name] = self._norm(text)
        return missing

    def by_text(self, text: str) -> np.ndarray | None:
        return self._items.get(self._norm(text))

    def by_name(self, name: str) -> np.ndarray | None:
        k = self._keys.get(name)
        return self._items.get(k) if k else None

    def __len__(self) -> int:
        return len(self._items)


# --- konuşmacı -------------------------------------------------------------------------------

@dataclass
class SpeakResult:
    ok: bool
    sentences: int = 0            # çalınan cümle sayısı
    first_audio_s: float | None = None   # say() çağrısından ilk sesin çalınmaya başlamasına kadar
    from_library: bool = False
    fallback_played: bool = False
    error: str = ""


class Speaker:
    def __init__(self, tts: TtsPort, library: PromptLibrary | None = None, timeout_s: float = 3.0,
                 fallback_prompt: str = "error"):
        self.tts, self.library, self.timeout_s = tts, library or PromptLibrary(), timeout_s
        self.fallback_prompt = fallback_prompt

    async def say(self, text: str, out: AudioOutPort) -> SpeakResult:
        t0 = time.perf_counter()
        ready = self.library.by_text(text)
        if ready is not None:
            await out.play(ready)
            return SpeakResult(True, 1, time.perf_counter() - t0, from_library=True)

        sentences = split_sentences(text)
        if not sentences:
            return SpeakResult(True)
        res = SpeakResult(True)
        nxt: asyncio.Task | None = asyncio.create_task(self._synth(sentences[0]))
        try:
            for i in range(len(sentences)):
                task, nxt = nxt, None
                try:
                    pcm = await task
                except (TtsError, TimeoutError) as e:
                    return await self._failed(res, out, f"cümle {i + 1}: {e or type(e).__name__}")
                if i + 1 < len(sentences):
                    nxt = asyncio.create_task(self._synth(sentences[i + 1]))  # çalarken sıradakini hazırla
                if res.first_audio_s is None:
                    res.first_audio_s = time.perf_counter() - t0
                await out.play(pcm)
                res.sentences += 1
            return res
        finally:
            if nxt is not None and not nxt.done():
                nxt.cancel()  # barge-in ya da hata: bekleyen seslendirme boşa çalışmasın

    async def _synth(self, sentence: str) -> np.ndarray:
        audio = await asyncio.wait_for(self.tts.synthesize(prepare_for_tts(sentence)), timeout=self.timeout_s)
        return to_line(audio)

    async def _failed(self, res: SpeakResult, out: AudioOutPort, error: str) -> SpeakResult:
        log.error("TTS hatası: %s", error)
        res.ok, res.error = False, error
        if res.sentences == 0 and (fb := self.library.by_name(self.fallback_prompt)) is not None:
            await out.play(fb)
            res.fallback_played = True
        return res


# --- düşünme sesi ----------------------------------------------------------------------------------------------

def keyboard_sound(seconds: float = 6.0, rate: int = 8000, level: float = 0.12, seed: int = 7) -> np.ndarray:
    """Asistan düşünürken çalınan klavye sesi (int16, mono). Kayıt değil, kodla üretilir: lisans sorunu yok.

    Her tuş: kısa gürültü patlaması → rastgele frekanslı rezonatör (1,2–3 kHz; boşluk tuşu daha pes ve uzun) →
    üstel sönüm. Yazma ritmi: 3–8 tuşluk gruplar, tuşlar arası ~90–200 ms, gruplar arası 0,3–0,7 sn duraklama.
    `level` tepe genliğidir (0,12 ≈ -18 dBFS): konuşmanın altında, arka planda kalır. Sabit tohum: her seferinde aynı.
    """
    from scipy.signal import lfilter

    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    out = np.zeros(n + rate // 10, np.float64)
    t = int(0.05 * rate)
    while t < n:
        for k in range(int(rng.integers(3, 9))):
            space = k > 0 and rng.random() < 0.15
            dur = int((0.045 if space else 0.025) * rate)
            tau = (0.012 if space else 0.006) * rate
            burst = rng.standard_normal(dur) * np.exp(-np.arange(dur) / tau)
            f = rng.uniform(500, 800) if space else rng.uniform(1200, 3000)
            r, w = 0.92, 2 * np.pi * f / rate
            body = lfilter([1 - r], [1, -2 * r * np.cos(w), r * r], burst)
            click = body * rng.uniform(0.6, 1.0)
            end = min(len(out), t + dur)
            out[t:end] += click[: end - t]
            t += int(rng.uniform(0.09, 0.2) * rate)
            if t >= n:
                break
        t += int(rng.uniform(0.3, 0.7) * rate)
    out = out[:n]
    peak = np.abs(out).max() or 1.0
    return (out / peak * level * 32767).astype(np.int16)
