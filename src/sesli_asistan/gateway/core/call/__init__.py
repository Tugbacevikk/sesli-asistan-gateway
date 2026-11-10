"""Tek bir çağrının yönetimi: durum makinesi (tasarim.md 5.1.1), akışlar (7.1–7.4), hata modları (9.1).

Bir çağrıda dört görev çalışır ve birbirini beklemez:
  1. pompa    : RTP kareleri → STT akışı (ses hiçbir zaman ağır bir işle aynı akışta okunmaz; 6.1)
  2. STT      : STT olaylarını olay kuyruğuna koyar
  3. bekçi    : her saniye "tik" (RTP boşta kalma, çağrı süresi, aktarma sonucu zaman aşımı)
  4. döngü    : olayları **sırayla** işler; durum yalnızca burada değişir (yarış koşulu yok)
Konuşma (TTS → RTP) ayrı bir görevdir; böylece arayan söze girince (SPEECH_STARTED) döngü onu iptal edebilir.

Kurallar (kodla):
  - Arayan, asistan konuşurken söze girerse seslendirme durur (barge-in, FR-11). Söze girilen cümle bir aktarma
    cümlesiyse ("Sizi Ayşe Yılmaz'a aktarıyorum") aktarma **yapılmaz**: arayan büyük olasılıkla düzeltiyor
    ("hayır, satış"); bekleyen aktarma silinir, yeni cümle normal işlenir (tasarim.md 7.5, ADR-012).
  - Operatöre aktarma söze girilse de yapılır (güvenli son çare).
  - STT "anlaşılamadı" derse diyalog "anlayamadım" der ve deneme sayacı artar; çok kısa gürültüler sayılmaz.
  - Arayan, asistan düşünürken konuşursa cümleler sıraya girer; sonraki turda birleştirilip işlenir.
  - STT akışı açılamaz ya da koparsa, santral eylemi başarısız olursa: hata anonsu + operatör (zarif bozulma).
  - Asistan düşünürken (LLM + rehber) `thinking_sound_delay_s`'den uzun sürerse arayana hafif bir klavye sesi
    çalınır; cevap hazır olunca o pakette kesilir. Telefonda 3–5 sn'lik sessizlik hattın koptuğu sanılmasın.
  - TTS cevabı seslendiremezse ve arayan cevaptan hiçbir şey duymadıysa operatöre aktarılır (yedek anons zaten
    "operatöre bağlıyorum" der; tasarim.md 9.1). Kısmen duyduysa çağrı sürer, art arda ikinci hatada operatör.
  - Arayan dinleme sırasında `no_input_s` boyunca hiç konuşmazsa "Sizi duyamıyorum…" denir; `no_input_max`'ıncı
    sessizlikte operatöre aktarılır (santral sessizken de RTP gönderdiği için RTP zaman aşımı bunu yakalamaz).

Modüller: `models` (durum, ayarlar, tur ve çağrı kayıtları), `session` (durum makinesi ve görevler)."""
from .models import (
    TRANSFER_STATUSES,
    CallConfig,
    CallState,
    CallStats,
    TurnRecord,
)
from .session import (
    CallSession,
)

__all__ = [
    "CallConfig",
    "CallSession",
    "CallState",
    "CallStats",
    "TRANSFER_STATUSES",
    "TurnRecord",
]
