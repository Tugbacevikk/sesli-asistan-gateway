"""Diyalog yöneticisi: LLM araç döngüsü + koda bağlı diyalog kuralları (tasarim.md 5.1, 7.5, 9).

LLM'e bırakılanlar: arayanın ne istediğini anlamak, aracı doğru parametreyle çağırmak, kısa ve
doğal bir cevap yazmak.
Koda bağlı olanlar (LLM unutsa ya da kandırılsa bile çalışır):
  - Aktarma yalnızca TransferPolicy'den geçerse yapılır (tools.py, policy.py).
  - **Net eşleşmede aktarma kodla yapılır (ADR-012):** InContacts `single_high` dönerse aktarma
    eylemi TransferPolicy'den geçirilip doğrudan üretilir ve sabit cümle söylenir; ikinci bir LLM turu
    beklenmez. (Ölçüm, 26.10.2026: qwen3.5:4b `single_high` sonrası çoğu zaman "aktarıyorum" deyip
    TransferCall'ı çağırmadı; ayrıca her tur bir LLM çağrısı demek.)
  - **İki deneme kuralı:** ilerleme olmayan her arayan turu sayılır (arama sonuçsuz ya da hiç arama/eylem
    yok); 2. başarısızlıkta operatör teklif edilir, 3.'de doğrudan operatöre aktarılır. Arayan hiçbir
    zaman döngüye hapsolmaz; LLM aramayı atlasa bile sayaç ilerler.
  - LLM hatası ya da zaman aşımında sabit anonsla operatöre aktarılır (zarif bozulma, NFR-08).
  - Araç turu sınırı: bir arayan cümlesinde en fazla `max_tool_rounds` LLM turu.
  - Konuşma geçmişi son `max_history` mesajla sınırlıdır (bağlam ve gecikme büyümesin).

Yapı (09.11.2026, ADR-034): `common` (sabitler, ayarlar, oturum, yardımcılar), `agent` (DialogAgent),
`steps` (akış adımları), `rules` (kural tabanlı çözme), `choice` ("hangisi?" ve seçim), `transfer` (aktarma,
teyit, operatör, sayaç), `llm` (LLM döngüsü)."""
from ..thresholds import MAX_BARGE_REPEATS
from .agent import DialogAgent
from .common import (
    _BRACKETS,
    _MARKDOWN,
    _THINK,
    DEPARTMENTS_HEAD,
    FEW_SHOT,
    GREETING_NO_ORG,
    STEPS,
    SYSTEM_PROMPT,
    DialogConfig,
    DialogSession,
    DialogTexts,
    _ancestor_line,
    _assistant_tool_message,
    _choice_said,
    _common_title,
    _common_unit,
    _exact_title,
    _last_user,
    _Run,
    _spoken_title,
    _tool_message,
    _Utterance,
    clean_reply,
    complete_sentences,
    first_sentences,
    fixed_prompts,
    greeting_text,
)

__all__ = [
    "MAX_BARGE_REPEATS",
    "DEPARTMENTS_HEAD",
    "DialogAgent",
    "DialogConfig",
    "DialogSession",
    "DialogTexts",
    "FEW_SHOT",
    "GREETING_NO_ORG",
    "STEPS",
    "SYSTEM_PROMPT",
    "_BRACKETS",
    "_MARKDOWN",
    "_Run",
    "_THINK",
    "_Utterance",
    "_ancestor_line",
    "_assistant_tool_message",
    "_choice_said",
    "_common_title",
    "_common_unit",
    "_exact_title",
    "_last_user",
    "_spoken_title",
    "_tool_message",
    "clean_reply",
    "complete_sentences",
    "first_sentences",
    "fixed_prompts",
    "greeting_text",
]
