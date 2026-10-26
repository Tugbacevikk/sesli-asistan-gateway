"""TTS önbelleği: bellek (LRU) + isteğe bağlı disk.

Neden: aynı cümleler çok tekrar eder ("Sizi Muhasebe birimine aktarıyorum."). Önbellekten gelen cümlenin
gecikmesi ~0'dır ve TTS'e yük bindirmez. Disk önbelleği, TTS servisi açılışta kapalı olsa bile daha önce
seslendirilmiş sabit anonsların yüklenebilmesini sağlar (tasarim.md 9.1).

Anahtar: sha256(ses kimliği | metin). Ses ya da hız değişince (`cache_id`) eski kayıtlar kullanılmaz.
Disk biçimi: 16 bit mono WAV (TTS'in kendi örnekleme hızında), dosya adı = anahtar.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import wave
from collections import OrderedDict
from pathlib import Path

from sesli_asistan.gateway.core.ports import Audio, TtsPort

from .piper_http import parse_wav

log = logging.getLogger(__name__)


class CachingTts:
    def __init__(self, inner: TtsPort, max_items: int = 256, disk_dir: str | Path | None = None,
                 cache_id: str | None = None):
        self.inner, self.max_items = inner, max_items
        self.cache_id = cache_id or getattr(inner, "cache_id", type(inner).__name__)
        self.disk = Path(disk_dir) if disk_dir else None
        if self.disk:
            self.disk.mkdir(parents=True, exist_ok=True)
        self._mem: OrderedDict[str, Audio] = OrderedDict()
        self.hits = self.misses = 0

    def key(self, text: str) -> str:
        return hashlib.sha256(f"{self.cache_id}|{text}".encode()).hexdigest()

    async def synthesize(self, text: str) -> Audio:
        k = self.key(text)
        if (a := self._mem.get(k)) is not None:
            self._mem.move_to_end(k)
            self.hits += 1
            return a
        if self.disk and (f := self.disk / f"{k}.wav").exists():
            try:
                a = parse_wav(await asyncio.to_thread(f.read_bytes))
                self.hits += 1
                self._remember(k, a)
                return a
            except Exception as e:  # bozuk dosya: yeniden seslendir
                log.warning("bozuk TTS önbellek dosyası %s: %s", f.name, e)
        self.misses += 1
        a = await self.inner.synthesize(text)
        self._remember(k, a)
        if self.disk:
            await asyncio.to_thread(_write_wav, self.disk / f"{k}.wav", a)
        return a

    def _remember(self, k: str, a: Audio) -> None:
        self._mem[k] = a
        self._mem.move_to_end(k)
        while len(self._mem) > self.max_items:
            self._mem.popitem(last=False)


def _write_wav(path: Path, a: Audio) -> None:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(a.rate)
        w.writeframes(a.pcm.astype("<i2").tobytes())
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(buf.getvalue())
    tmp.replace(path)  # yarım dosya kalmasın
