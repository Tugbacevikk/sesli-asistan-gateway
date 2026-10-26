"""Metin test arayüzü: asistanla yazarak konuş (ses yok). Gerçek LLM (Ollama) ve örnek rehberle çalışır.

    python tools/sohbet/sohbet.py
    python tools/sohbet/sohbet.py --model qwen3.5:4b --rehber data/rehber_ornek.json

Komutlar (santral olaylarını taklit eder):
    /basarili   son aktarma cevaplandı
    /mesgul     son aktarma: meşgul
    /cevapsiz   son aktarma: cevap yok
    /yeni       yeni çağrı
    /cik        çıkış
Boş satır: STT'nin boş metin vermesi (anlaşılmayan konuşma) gibi davranır.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from sesli_asistan.gateway.composition import build_agent, build_directory  # noqa: E402
from sesli_asistan.gateway.config import GatewaySettings  # noqa: E402

EVENTS = {"/basarili": "answered", "/mesgul": "busy", "/cevapsiz": "no_answer"}


def show(turn, ms: float) -> None:
    if turn.text:
        print(f"asistan> {turn.text}")
    for a in turn.actions:
        print(f"   [santrale eylem] {a.type} {getattr(a, 'extension', '')}".rstrip())
    if turn.ended:
        print("   [çağrı aktarıldı, oturum bitti]")
    print(f"   ({ms:.0f} ms{', sabit metin' if turn.fallback else ''})")


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=None)
    ap.add_argument("--rehber", default=None)
    ap.add_argument("--ayrinti", action="store_true", help="araç çağrılarını göster")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.ayrinti else logging.WARNING, format="   %(message)s")

    s = GatewaySettings()
    if a.model:
        s.llm_model = a.model
    if a.rehber:
        s.directory_path = a.rehber
    directory = build_directory(s)
    if not await directory.refresh():
        print("rehber okunamadı", file=sys.stderr)
        return 1
    agent = build_agent(s, directory)
    print(f"model: {s.llm_model} ({s.llm_backend}) · rehber: {len(directory.index)} kayıt · /cik ile çık\n")

    session = agent.new_session("sohbet-1")
    show(session.greeting(), 0)
    n = 1
    while True:
        try:
            line = input("arayan> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        cmd = line.strip()
        if cmd == "/cik":
            return 0
        if cmd == "/yeni":
            n += 1
            session = agent.new_session(f"sohbet-{n}")
            show(session.greeting(), 0)
            continue
        t = time.perf_counter()
        turn = await (session.handle_transfer_result(EVENTS[cmd]) if cmd in EVENTS else session.handle_user(line))
        show(turn, (time.perf_counter() - t) * 1000)
        if turn.ended:
            n += 1
            session = agent.new_session(f"sohbet-{n}")
            print()
            show(session.greeting(), 0)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
