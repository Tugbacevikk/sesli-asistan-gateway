"""Söze girmede ses seviyesi kapısı: arka plan sesi (odadaki video, ikinci konuşan) asistanın sözünü kesmesin.

Canlı ölçüm (05.11.2026, MicroSIP, aynı mikrofon): arayan -29 / -31 dBFS (ortalama), arkadaki video -37 / -38 dBFS;
yaklaşık 7–9 dB fark. Ses algılayıcı (VAD) ikisini de konuşma sayıyor; ayırt edici olan seviye. Kapı çağrı içinde
arayanın kendi seviyesini öğrenir (anlaşılan sözlerinden) ve söze girme anındaki son 300 ms bunun `margin_db`
altındaysa söze girmeyi saymaz: asistan konuşmasını sürdürür. Arayanın seviyesi henüz bilinmiyorsa (ilk söz) yalnız
çok kısık sesler (`floor_db`) elenir. Yalnız söze girmeyi etkiler: arayan asistan sustuktan sonra her seviyede
dinlenir.
"""
from __future__ import annotations

from collections import deque

import numpy as np

from sesli_asistan.libs.audio import g711

FRAME_S = 0.02
_WINDOW_S = 0.3          # söze girme anındaki pencere
_KEEP_S = 8.0            # seviyesi tutulan son ses (bir sözü kapsayacak kadar)
_VOICED_DB = -50.0       # bunun altı sessizlik: arayan seviyesine katılmaz


def frame_db(frame: bytes, codec: str) -> float:
    """20 ms karenin RMS seviyesi (dBFS). G.711 baytları çözülür; G.722 alıcıda çözülmüş PCM16 gelir."""
    if codec in g711.CODECS:
        x = g711.decode(frame, codec)
    else:
        x = np.frombuffer(frame[: len(frame) // 2 * 2], dtype="<i2").astype(np.float32) / 32768
    if not len(x):
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return 20 * np.log10(max(rms, 1e-6))


class LevelGate:
    def __init__(self, margin_db: float = 6.0, floor_db: float = -45.0, min_speech_s: float = 0.6):
        """`margin_db` 0: kapı kapalı."""
        self.margin_db, self.floor_db, self.min_speech_s = margin_db, floor_db, min_speech_s
        self._frames: deque[tuple[float, float]] = deque()      # (zaman, dBFS)
        self._caller: deque[float] = deque(maxlen=400)          # arayanın sesli kareleri (anlaşılan sözlerden)

    def add(self, at: float, db: float) -> None:
        self._frames.append((at, db))
        while self._frames and self._frames[0][0] < at - _KEEP_S:
            self._frames.popleft()

    def learn(self, start: float, end: float) -> None:
        """Anlaşılan bir söz (arayanın kendi sesi): o aralığın sesli kareleri arayanın seviyesine katılır."""
        self._caller.extend(db for t, db in self._frames if start <= t <= end and db > _VOICED_DB)

    @property
    def caller_db(self) -> float | None:
        """Arayanın konuşma seviyesi (sesli karelerin ortancası); yeterli konuşma yoksa None."""
        if len(self._caller) * FRAME_S < self.min_speech_s:
            return None
        return float(np.median(self._caller))

    def recent_db(self, at: float) -> float | None:
        """Söze girme anındaki seviye: son 300 ms'nin **sesli** karelerinin ortancası (sessiz kareler sayılmaz:
        paketler toplu gelirse pencereye sözden sonraki sessizlik de girer)."""
        recent = [db for t, db in self._frames if t >= at - _WINDOW_S and db > _VOICED_DB]
        return float(np.median(recent)) if recent else None

    def utterance_db(self, start: float, end: float) -> float | None:
        """Bir sözün seviyesi: o aralıktaki sesli karelerin ortancası."""
        voiced = [db for t, db in self._frames if start <= t <= end and db > _VOICED_DB]
        return float(np.median(voiced)) if voiced else None

    def is_background(self, start: float, end: float, margin_db: float) -> bool:
        """Söz, arayanın kendi seviyesinin `margin_db` altında mı (odadaki başka biri, uzaktan konuşma)? Arayanın
        seviyesi bilinmiyorsa ya da sözün ses bilgisi yoksa False: emin olunmadan hiçbir söz atılmaz."""
        ref, level = self.caller_db, self.utterance_db(start, end)
        return margin_db > 0 and ref is not None and level is not None and level < ref - margin_db

    def allows_barge_in(self, at: float) -> bool:
        if self.margin_db <= 0:
            return True
        level = self.recent_db(at)
        if level is None:
            return True                                    # ses bilgisi yok: eskisi gibi
        ref = self.caller_db
        limit = self.floor_db if ref is None else max(self.floor_db, ref - self.margin_db)
        return level >= limit
