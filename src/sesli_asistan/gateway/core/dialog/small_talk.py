"""Kısa sohbet (ADR-035, `SMALL_TALK=true`): hal hatıra araçsız tek LLM çağrısıyla sıcak bir cümle.
Adım `steps.StepsMixin._small_talk_turn`; burada istem ve cümle denetimi.

Neden araçsız: ince ayarlı anlama modeli (gemma-santral, ADR-038) araç desteklemez; eski yolda (araçlı LLM döngüsü)
hal hatır sözü araç retti yüzünden kural turuna düşüp sabit cümle alıyordu. Burada model yalnız bir karşılık cümlesi
üretir; "Kiminle görüşmek istersiniz?" sorusunu kod ekler. Cümle denetimden geçmezse (boş, uzun, soru, rakam, aktarma
sözü, JSON) kodun selam cevabı verilir: LLM sohbet ederken kimseye aktarma sözü veremez.

İstem eğitim verisiyle birebir aynıdır (`tools/egitim/anlama_veri.py` `sohbet_ornekleri`): değişirse model yeniden
eğitilmelidir.
"""
from __future__ import annotations

import re

SMALL_TALK_PROMPT = """Sen bir santralin sesli asistanısın. Arayan selam verdi, hal hatır sordu ya da kısa bir sohbet
cümlesi söyledi. Ona Türkçe, sıcak, doğal ve kısa tek bir cümleyle karşılık ver.
- Soru sorma, kimseyi ya da hiçbir birimi önerme, ad, numara ya da dahili söyleme.
- Yalnız karşılık cümlesini yaz; tırnak, emoji, liste kullanma."""

MAX_CHARS = 110
_BANNED = re.compile(r"[{}\[\]<>*#\"0-9?]|aktar|bağla|dahili|numara|operatör|yönlendir", re.IGNORECASE)


def clean_small_talk(raw: str) -> str | None:
    """Modelin cevabı telefonda okunabilir tek bir sıcak cümle mi? Değilse None (kod cevabı verilir)."""
    text = " ".join((raw or "").split()).strip(" '\"“”‘’")
    if not text:
        return None
    first = re.match(r"(.+?[.!])(\s|$)", text)          # yalnız ilk cümle ("… teşekkür ederim. Size nasıl…")
    text = (first.group(1) if first else text).strip()
    if len(text) > MAX_CHARS or len(text.split()) < 2 or _BANNED.search(text):
        return None
    if not text.endswith((".", "!")):
        text += "."
    return tr_capitalize(text)


def tr_capitalize(text: str) -> str:
    """İlk harf büyük, Türkçe kuralıyla ("iyiyim" → "İyiyim", "ılık" → "Ilık"; Python "i"yi "I" yapar)."""
    first = {"i": "İ", "ı": "I"}.get(text[:1], text[:1].upper())
    return first + text[1:]

