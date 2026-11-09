"""Diyalog ve çağrı eşikleri: tek yerde, her biri dayandığı ölçüm ya da canlı denemeyle (tasarim.md Ek E).

Ayardan değiştirilebilenler burada **değil**, `config.py`'de (ortam değişkeni) ve tasarim.md 13'te:
  CONFIRM_BELOW 0,75 (STT güveni altındaysa aktarmadan önce teyit), REPEAT_BELOW 0,5 (altında LLM yok, tekrar
  istenir), MATCH_MIN_SCORE 70 / MATCH_HIGH_SCORE 90 / MATCH_MARGIN 5 (rehber araması), FAST_PATH_MIN_SCORE,
  STT tarafında VAD_THRESHOLD 0,3 ve SPEECH_STARTED_MS 300 (söze girme).
Buradakiler kurumdan kuruma değişmesi beklenmeyen, senaryo setleriyle (tests/data/diyalog_seti*.yaml) kalibre
edilmiş değerlerdir. Değiştirmeden önce iki set de çalıştırılmalı (`tests/eval/diyalog.py --kural --mcp-adimi`).

Benzerlik puanları rapidfuzz ölçeğinde (0–100), katlanmış sözcükler üzerinde (`search.similarity`).
"""
from __future__ import annotations

# --- sözcük benzerliği -----------------------------------------------------------------------------------------
# Bir sözcük kişinin adının / soyadının kendisi (ekli ya da tek harf hatalı: "yılmazla", "öztürk'ü"). Bunun altında
# başka adlar karışıyor ("demir" ~ "deniz" 80).
NAME_SAME = 92.0
# "X mı, Y mi?" cevabında soyad ya da birim sözcüğü ("yılmaz olan", "muhasebedeki").
CHOICE_WORD = 90.0
# Seçim cevabı hiçbir adayı tam söylemiyorsa soyada en yakın aday **teyitle** sorulur: en az bu puan ve öbür
# adaylardan bu kadar önde (canlı 03.11.2026: "Ayşe Yılmaz" → STT "her şey uymaz"; yanlış seçim zaten teyitle sorulur).
FUZZY_CHOICE_MIN = 70.0
FUZZY_CHOICE_MARGIN = 10.0
# Seçim cevabında adın kendisi (adaylar aynı adlı: "Ayşe") soyad sayılmasın.
FIRST_NAME_LIKE = 80.0
# Kısa cevap "evet"e benziyor ("evt", "eve"; teyit sorusunda).
YES_LIKE = 75.0
# "operatör"ün STT bozulmaları: kesin istek ("oparatöre", "operetör") ve operatör teyidi sorulacak belirsiz biçim.
OPERATOR_LIKE = 85.0
OPERATOR_MAYBE = 70.0

# Kural modunda tam uyan yokken tek kişinin adına yakın söz teyitle sorulur ("ahmet bey" → Mehmet Demir 80, D14);
# "veli" → Elif 77 sorulmaz (D16).
NEAR_NAME_MIN = 80.0

# --- LLM hedefinin sözde geçmesi (canlı 05.11.2026) ---------------------------------------------------------------
# Düşük güvenli sözle LLM'in seçtiği kişinin adı / soyadı / unvanı / birimi sözde en az bu benzerlikte geçmeli; yoksa
# teyit de sorulmaz, "bulamadım". "çokağa" ~ "kaya" 67,5 (geçmez), "kayayla" ~ "kaya" 100.
GROUNDED_MIN = 75.0

# Bozuk duyulan birim adı ("masebeye" ~ "muhasebe" 85,7), yalnız teyitle; "masa" / "mesaj" ~ "muhasebe" 40–50.
FUZZY_TARGET_MIN = 82.0

# --- ses benzerliği (ADR-026, ölçüm 04.11.2026) -------------------------------------------------------------------
# (yazım + ünsüz iskeleti benzerliği) / 2. Gerçek STT hatalarından 6/8'i doğru, ad içermeyen 29 sözün hiçbiri
# tetiklemedi (en yakın: "fiyat paketi" 68,6 / fark 15). Her zaman teyitle.
SOUND_MIN = 65.0
SOUND_MARGIN = 25.0

# --- söze girme ve gürültü (canlı 04–05.11.2026) ------------------------------------------------------------------
# Bundan kısa "anlaşılamadı" (boş) sesler gürültü (öksürük, tık): cevaplanmaz, deneme sayılmaz.
MIN_NOISE_MS = 600
# Gürültü kırıntısı: güveni bunun altında ve en çok bu kadar harf ("b" 0,05; "s" 0,23 geçer, tekrar kapısına düşer).
NOISE_BELOW = 0.2
NOISE_MAX_LETTERS = 2
# Söze girmede ses seviyesi kapısı (core/level.py): son 300 ms arayanın kendi seviyesinin bu kadar altındaysa
# söze girme sayılmaz. Canlı 05.11.2026: arayan -29 / -31 dBFS, arkadaki video -37 / -38 dBFS (7–9 dB fark).
# Seviyesi henüz bilinmeyen arayanda yalnız BARGE_IN_FLOOR_DB altı; arayanın seviyesi güveni LEVEL_LEARN_CONF
# üstündeki sözlerinden öğrenilir. 0: kapalı (BARGE_IN_LEVEL_MARGIN_DB).
BARGE_IN_LEVEL_MARGIN_DB = 6.0
BARGE_IN_FLOOR_DB = -45.0
LEVEL_LEARN_CONF = 0.5
# Arka plan konuşması (09.11.2026, ofiste deneme): arayanın öğrenilmiş seviyesinin bu kadar dB altındaki söz
# cevaplanmaz, deneme sayılmaz. Arayanın seviyesi henüz bilinmiyorken (ilk anlaşılan sözden önce) çalışmaz. 0: kapalı.
BACKGROUND_SPEECH_MARGIN_DB = 8.0
# Söze girme anlaşılmadığında yarıda kalan cümle üst üste en çok bu kadar yinelenir (sonra olağan "anlayamadım").
MAX_BARGE_REPEATS = 2

# --- aday sayıları ------------------------------------------------------------------------------------------------
MAX_PERSON_CANDIDATES = 3   # kişi çözme: "hangisi?" en çok 3 aday (telefonda dördüncü akılda kalmıyor)
MAX_TOPIC_CANDIDATES = 5    # konu → birim
MAX_REPLY_SENTENCES = 2     # LLM cevabı telefonda (canlı 03.11.2026: "seni ara" → 3 cümle, 8,5 sn)
