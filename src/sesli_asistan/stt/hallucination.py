"""Whisper uydurma süzgeci.

Kaynak: Vietnamese-speech-to-text-models
  - `infrastructure/catalog.py` → `DROP_PHRASES["tr"]` (gözlenmiş Türkçe uydurma cümleleri)
  - `infrastructure/faster_whisper_asr.py` → `_key`, `drop_repeats`

Neden önemli (tasarim.md 5.2.1, R-10): Whisper sessizlik/gürültüde video altyazısı kalıplarını
yazabiliyor. Asistanda bu, var olmayan bir "isteği" LLM'e götürür.

Not: Ölçümlerde görülen "teşekkürler", "hoşça kalın" gibi uydurmalar listeye konmadı; bunlar
telefonda gerçekten söylenebilir. Onlara karşı koruma: Whisper'a yalnızca VAD'ın konuşma bulduğu
ses verilmesi + `no_speech_threshold` / `log_prob_threshold`.
"""
from __future__ import annotations

from collections.abc import Iterable

from sesli_asistan.libs.text.normalizer import turkish_lower

DROP_PHRASES_TR: tuple[str, ...] = (
    "İzlediğiniz için teşekkür ederim.", "İzlediğiniz için teşekkürler.", "Altyazı M.K.",
    "Altyazı", "Abone olmayı unutmayın.", "Abone olun.", "Videoyu beğenmeyi unutmayın.",
    "Bir sonraki videoda görüşmek üzere.", "Müzik", "Thanks for watching!",
    "Subtitles by the Amara.org community",
)


def key(text: str) -> str:
    """Karşılaştırma anahtarı: yalnızca harf ve rakam, küçük harf ("Abone olun." → "aboneolun")."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


class HallucinationFilter:
    def __init__(self, phrases: Iterable[str] = DROP_PHRASES_TR, max_repeat: int = 2):
        self._drop = {key(p) for p in phrases}
        self.max_repeat = max_repeat

    def add(self, phrase: str) -> None:
        """Ör. Whisper'a verilen istem (initial_prompt) sessiz yerde aynen yazılırsa süzülsün."""
        if phrase:
            self._drop.add(key(phrase))

    def is_hallucination(self, text: str) -> bool:
        k = key(text)
        return not k or k in self._drop

    def drop_repeats(self, words: list[str]) -> list[str]:
        """Aynı kelime art arda max_repeat'ten fazla gelirse fazlasını atar
        ("evet evet evet evet" → "evet evet"): Whisper'ın döngüye girmesine karşı."""
        out: list[str] = []
        run = 0
        for w in words:
            run = run + 1 if out and key(out[-1]) == key(w) else 1
            if run <= self.max_repeat:
                out.append(w)
        return out


def strip_hotword_echo(text: str, hotwords: Iterable[str]) -> tuple[str, str]:
    """İpucu yankısı: Whisper ipucu listesini (rehber adları, sırasıyla) önceki metin olarak alır; kısa bir sözden
    sonra listenin devamını da yazabiliyor. Gözlem (02.11.2026, santral): arayan "Ayşe Yılmaz" dedi, ipucu
    "Ayşe Yılmaz Zeynep Arslan …" iken metin "ayşe yılmaz zeynep arslan" geldi; asistan iki kişi duydu.

    Kural: metin, listede art arda gelen girişlerle BİTİYORSA (E_i E_i+1 … E_j, j > i) ilk girişten sonrası atılır.
    Arayanın gerçekten iki adı arka arkaya söylemesi ve bunların listede de tam o sırayla durması nadirdir; olursa
    asistan ilk kişiyi anlar (aktarmadan önce zaten adı söyler). (kalan metin, atılan kısım) döner."""
    entries = [e for e in (key_words(h) for h in hotwords) if e]
    words = text.split()
    kw = [key(turkish_lower(w)) for w in words]
    if len(entries) < 2 or len(kw) < 2:
        return text, ""
    best = len(kw)
    for i in range(len(entries) - 1):
        # metnin sonundan geriye: E_j … E_{i+1} listede art arda, önlerinde E_i
        for j in range(len(entries) - 1, i, -1):
            tail = [w for e in entries[i + 1:j + 1] for w in e]
            head = entries[i]
            n, m = len(tail), len(head)
            if n + m <= len(kw) and kw[len(kw) - n:] == tail and kw[len(kw) - n - m:len(kw) - n] == head:
                best = min(best, len(kw) - n)
    if best == len(kw):
        return text, ""
    return " ".join(words[:best]), " ".join(words[best:])


def key_words(text: str) -> list[str]:
    return [k for k in (key(turkish_lower(w)) for w in text.split()) if k]
