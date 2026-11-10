"""Rehber yükleme (tasarim.md 13.2): müşterinin Excel / CSV rehberi → rehber dosyası (DIRECTORY_PATH).

Akış: dosya okunur → sütunlar başlıktan tanınır → her satır doğrulanır → önizleme (eklenen / silinen / değişen,
hatalı satırlar, uyarılar) → yönetici onaylar → rehber dosyası atomik yazılır (önceki sürüm `.onceki` olarak
saklanır, tek adımda geri alınabilir) → rehber yeniden okunur. Yerel takma adlar kayıt kimliğine bağlıdır: aynı
kişi (tür + ad + dahili) yeniden yüklendiğinde kimliği korunur, takma adları kopmaz.

Biçimler:
  - .xlsx (Excel 2007+): ilk sayfa. Yalnız standart kütüphane (zipfile + ElementTree): ek bağımlılık yok; boyut,
    sıkıştırma oranı ve DOCTYPE (XML varlık saldırıları) sınırlanır.
  - .csv: UTF-8 (BOM'lu ya da değil) ya da Windows-1254 (Türkçe Excel'in "CSV" kaydı); ayraç ; , ya da sekme.
  - Eski .xls kabul edilmez: "Farklı kaydet → .xlsx" denir.

Sütun adları büyük/küçük harf ve Türkçe karakterden bağımsızdır (ör. "Adı", "AD", "isim"; "Dahili No", "Extension").

Modüller: `common` (sınırlar, sütunlar, sonuç tipleri), `table` (Excel / CSV okuma), `rows` (satırları kayda
çevirme), `compare` (birleştirme, fark, dışa aktarma)."""
from .common import (
    MAX_FILE,
    ImportResult,
    Problem,
    fold,
)
from .compare import (
    diff,
    export_csv,
    keep_unchanged_timestamps,
    merge,
    template_csv,
)
from .rows import (
    looks_like_person,
    parse,
)
from .table import (
    read_table,
)

__all__ = [
    "ImportResult",
    "MAX_FILE",
    "Problem",
    "diff",
    "export_csv",
    "fold",
    "keep_unchanged_timestamps",
    "looks_like_person",
    "merge",
    "parse",
    "read_table",
    "template_csv",
]
