"""Servislerin ortak log biçimi (tasarim.md 12.1).

`LOG_FORMAT=json` (üretim, journald): her satır tek JSON nesnesi — ts, level, service, logger, call_id, msg (+ hata).
`LOG_FORMAT=text` (geliştirme, varsayılan): okunur düz satır.
Çağrı kimliği, çekirdeğin log satırlarındaki "[call_id] ..." ön ekinden alınır; çekirdek log altyapısını bilmez.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from contextvars import ContextVar

_CALL = re.compile(r"^\[([^\]\s]{1,64})\]\s")
# Çağrı oturumunun görevlerinde geçerli çağrı kimliği (oturum başında kurulur; alt görevler ve iş parçacıkları
# devralır): "[call_id]" ön eki olmayan satırlar da ("araç InContacts(…)") doğru çağrıya bağlanır
CURRENT_CALL: ContextVar[str | None] = ContextVar("current_call", default=None)


def call_id_of(record: logging.LogRecord) -> str | None:
    """Satırın çağrı kimliği: "[call_id] …" ön eki, yoksa o an çalışan çağrı oturumu."""
    m = _CALL.match(record.getMessage())
    return m.group(1) if m else CURRENT_CALL.get()
_SECRET = re.compile(r"(anahtar=)[^&\s\"']+", re.I)


class RedactSecrets(logging.Filter):
    """Adres satırındaki anahtarı (`?anahtar=…`) loglardan siler (uvicorn erişim logu dahil)."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "anahtar=" in msg.lower():
            record.msg, record.args = _SECRET.sub(r"\1***", msg), ()
        return True


def log_text(text: str) -> str:
    """Arayanın sözü / cevap metni loga yazılırken: `LOG_TEXTS=false` ise yalnız uzunluk (kişisel veri, KVKK)."""
    if os.environ.get("LOG_TEXTS", "true").lower() in ("0", "false", "no", "hayir"):
        return f"<{len(text or '')} harf>"
    return repr(text)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        m = _CALL.match(msg)
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)) + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "call_id": m.group(1) if m else CURRENT_CALL.get(),
            "msg": msg[m.end():] if m else msg,
        }
        if record.exc_info:
            out["error"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def setup_logging(service: str, fmt: str | None = None, level: str | None = None) -> None:
    fmt = (fmt or os.environ.get("LOG_FORMAT", "text")).lower()
    handler = logging.StreamHandler()
    if fmt == "json":
        handler.setFormatter(JsonFormatter(service))
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactSecrets())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel((level or os.environ.get("LOG_LEVEL", "INFO")).upper())
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
