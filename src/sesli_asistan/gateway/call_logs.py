"""Çağrı başına kayıt dosyası: her konuşma bitince o çağrının bütün log satırları ve özeti ayrı dosyaya yazılır.

    <CALL_LOG_DIR>/<YYYY-AA-GG>/<SSddss>_<call_id>.log   (saat-dakika-saniye, çağrının bittiği an)

Dosyada önce log satırları (çağrı açıldı → duyulan sözler → kurallar / araçlar / LLM → cevaplar → santral eylemleri →
bitiş), sonra `--- özet ---` ve çağrı özeti (JSON: sonuç, süre, turlar, söze girmeler, arka plan sayılan sözler …).
Terminal kaydı (journald) ayrıca sürer; bu dosyalar bir konuşmayı sonradan baştan sona okumak içindir.

Satır çağrıya iki yoldan bağlanır: "[call_id] …" ön eki ya da çağrı oturumunun görevinde geçerli kimlik
(`obs.CURRENT_CALL`; ön eksiz satırlar: "araç InContacts(…)"). Metinler `LOG_TEXTS` ayarına uyar (kişisel veri).
Konuşma satırları INFO düzeyinde: `LOG_LEVEL` INFO (varsayılan) ya da daha ayrıntılı olmalı.
Eski günler `CALL_HISTORY_DAYS`'ten sonra silinir (çağrı geçmişiyle aynı saklama süresi).
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sesli_asistan.libs.obs import call_id_of

MAX_LINES = 5000                      # bir çağrıda tutulan en çok satır (takılan çağrı belleği doldurmasın)
_FORMAT = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")


class CallLogRecorder(logging.Handler):
    """`sesli_asistan` loglarını çağrı başına biriktirir; `flush` çağrı bitince dosyaya yazar."""

    def __init__(self, folder: str | Path):
        super().__init__(level=logging.INFO)
        self.folder = Path(folder)
        self._lines: dict[str, list[str]] = {}
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        call_id = call_id_of(record)
        if call_id is None:
            return
        try:
            line = _FORMAT.format(record)
        except Exception:                    # biçimlenemeyen satır kaydı bozmasın (logging kuralı: emit hata atmaz)
            self.handleError(record)
            return
        with self._guard:
            lines = self._lines.setdefault(call_id, [])
            if len(lines) < MAX_LINES:
                lines.append(line)

    def flush_call(self, call_id: str, stats: Any = None) -> Path | None:
        """Çağrının satırlarını ve özetini dosyaya yazar, belleği boşaltır. Yazılan dosya (satır yoksa None)."""
        with self._guard:
            lines = self._lines.pop(call_id, [])
        if not lines and stats is None:
            return None
        now = datetime.now()
        day = self.folder / now.strftime("%Y-%m-%d")
        day.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in call_id)[:64]
        path = day / f"{now.strftime('%H%M%S')}_{safe}.log"
        summary = asdict(stats) if is_dataclass(stats) and not isinstance(stats, type) else stats
        with path.open("w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            if summary is not None:
                f.write("--- özet ---\n")
                f.write(json.dumps(summary, ensure_ascii=False, indent=1, default=str) + "\n")
        return path

    def purge(self, days: int, today: date | None = None) -> int:
        """`days` günden eski gün klasörlerini siler; silinen klasör sayısı."""
        limit = (today or date.today()) - timedelta(days=days)
        removed = 0
        if not self.folder.is_dir():
            return 0
        for d in self.folder.iterdir():
            try:
                day = datetime.strptime(d.name, "%Y-%m-%d").date()
            except ValueError:
                continue                     # elle konmuş başka bir şey: dokunulmaz
            if d.is_dir() and day < limit:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        return removed


def attach(folder: str) -> CallLogRecorder | None:
    """`sesli_asistan` loglarına bağlanır (kök loga değil: `setup_logging` kök işleyicileri yeniden kurar)."""
    if not folder.strip():
        return None
    rec = CallLogRecorder(folder.strip())
    logging.getLogger("sesli_asistan").addHandler(rec)
    return rec


def detach(rec: CallLogRecorder | None) -> None:
    if rec is not None:
        logging.getLogger("sesli_asistan").removeHandler(rec)

