"""Kişi çözme: akışın 2. adımı (ADR-025, MCP `kisi_coz`).

Akış: 1. rehber (hızlı yol: tam ad, birim hattı, dahili, "hangisi?") → 2. MCP (`kisi_coz`, `konu_ara`) → 3. LLM.
1. adım yalnız kesin durumlarda karar verir; şu üç durumda kenara çekilir ve söz eskiden LLM'e giderdi (3–6 sn).
Rehberdeki bilgi yeter, kurallar burada:

  1. Unvan ("İl Jandarma Komutanı", "muhasebe müdürü", "vali yardımcısı"): sözde unvan sözcüğü var ve unvanla tek
     kişi bulunuyor → o kişi (kesin). 2–3 kişi → hangisi sorulur.
  2. Birim + ad ("muhasebeden Ayşe Hanım"): söylenen birimde o ad / soyadla tek kişi → o kişi (kesin).
  3. Ad + hitap ya da yalnız ad ("Mehmet Bey"): rehberde o ad / soyadla tek kişi → o kişi, **kesin değil** (arayana
     teyit sorulur: kişinin kendisi mi, aynı adlı rehberde olmayan biri mi bilinmez). Birden çok → hangisi.

Arayanın kendini tanıttığı ad ("ben Mehmet, …", "adım Ali") aday sayılmaz. Gateway ve referans MCP sunucusu aynı
işlevi kullanır: MCP düşerse Gateway aynısını kendisi çalıştırır (LLM'e düşmez).

Modüller: `common` (ortak sözcük işlemleri), `names` (kişi adı), `departments` (birim adı),
`titles` (unvan ve makam hattı), `resolve` (sözden kişi / hedef çözme)."""
from .departments import (
    department_said,
    fuzzy_departments,
)
from .names import (
    has_honorific,
    said_surely,
    sound_alike,
)
from .resolve import (
    Mentions,
    mentioned_targets,
    resolve_person,
)
from .titles import (
    narrow_candidates,
    same_title,
    same_unit,
)

__all__ = [
    "Mentions",
    "department_said",
    "fuzzy_departments",
    "has_honorific",
    "mentioned_targets",
    "narrow_candidates",
    "resolve_person",
    "said_surely",
    "same_title",
    "same_unit",
    "sound_alike",
]
