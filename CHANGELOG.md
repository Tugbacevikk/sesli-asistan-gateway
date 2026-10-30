# Değişiklik günlüğü

Biçim [Keep a Changelog](https://keepachangelog.com/tr/1.1.0/), sürüm numaraları [Anlamsal Sürümleme](https://semver.org/lang/tr/)
(tasarim.md 14.9):

- **MAJOR**: santral sözleşmesi (`/v1` REST, RTP) ya da ayar adları uyumsuz değişir; geri alınamayan veri geçişi.
- **MINOR**: yeni özellik; ayarlar ve veriler geriye uyumlu (veritabanı geçişleri yalnız ekler).
- **PATCH**: hata düzeltmesi.

Yeni değişiklikler **Yayımlanmamış** başlığına yazılır. Sürüm çıkarmak: `python tools/kurulum/surum.py hazirla 0.2.0`
(başlığı sürüm ve tarihe çevirir, `pyproject.toml`'u günceller), commit, `git tag v0.2.0`, `git push origin v0.2.0`.
Etiket CI'da denetlenir ve GitHub sürümü (kaynak + wheel + bu notlar) oluşturulur. İnternetsiz kurulum paketi imza
anahtarının bulunduğu makinede `deploy/paket.sh --imza <anahtar>` ile üretilir.

## [Yayımlanmamış]

### Eklendi
- Arayüzden rehber yükleme: Excel (.xlsx) ya da CSV; sütunlar başlıktan tanınır, önizlemede eklenen / silinen /
  değişen kayıtlar, satır numaralı hatalar ve uyarılar; onaydan sonra atomik yazma, tek adımda geri alma; mevcut
  rehberi ve boş şablonu CSV olarak indirme. Kayıt kimlikleri korunur (yerel takma adlar kopmaz).
- Çağrı geçmişi veritabanında şema sürümü ve otomatik geçişler (`PRAGMA user_version`); yalnız ekleyen geçişler,
  yarıda kalan geçiş geri alınır, yeni sürümün veritabanında eski sürüm çalışmaya devam eder.
- `install.sh --kaldir` (ayarlar ve veriler kalır) ve `--kaldir --verilerle` (önce son yedek, sonra hepsi).
- Paket imzası: `paket.sh --imza` GPG ile imzalar; `install.sh --paket` imzayı paketten gelmeyen anahtarla
  (`--imza-anahtari`, ilk kurulumda bir kez) doğrulamadan kurmaz.
- Destek paketi: `tools/kurulum/destek.py` (sürüm, sistem, servisler, sağlık, metrikler, loglar, maskelenmiş ayarlar;
  gizli değerler, adlar, konuşma metinleri ve numaralar girmez).
- Sürüm yönetimi: bu dosya, `tools/kurulum/surum.py`, etiketten GitHub sürümü oluşturan CI işi; sürüm sağlık
  yanıtında (`version`).

### Değişti
- Rehber dosyası varsayılan yeri `/var/lib/sesli-asistan/rehber.json` (Gateway yazabilsin diye; `/etc` systemd'de
  salt okunur). Eski kurulumlarda `/etc` altındaki dosya salt okunur çalışmaya devam eder.
- `transfers` tablosuna tarih dizini (geçiş 2).

## [0.1.0] — 2026-10-30

İlk etiketli sürüm: bu noktaya kadar geliştirilen her şey.

- Santral entegrasyonu: REST + RTP (G.711), çağrı oturumu durum makinesi, söze girme, aktarma ve sonuçları
  (meşgul / cevapsız / başarısız), operatöre aktarma, tuşlama, boşaltarak kesintisiz yeniden başlatma.
- Konuşma tanıma: gRPC STT servisi (Silero VAD + faster-whisper medium), rehberden ipuçları, uydurma süzgeci.
- Diyalog: hızlı yol (ADR-016), belirsizlikte kodla soru (ADR-017), akışlı LLM cevabı (ADR-018), kodla cevaplar ve
  LLM'siz kural modu (ADR-019), LLM düşerse kural moduna geçiş (ADR-020); Türkçe bulanık rehber araması.
- Seslendirme: Piper (ve isteğe bağlı Seslen) ile cümle cümle seslendirme, anons kütüphanesi, sınırlı disk önbelleği.
- Arayüz: tarayıcıdan deneme çağrısı, izleme, rehber, ayarlar (anonslar, mesai, tatiller, takma adlar, yerel
  kayıtlar), roller ve değişiklik geçmişi.
- İşletim: Prometheus metrikleri, alarm kuralları, Grafana panosu, JSON log, devre kesiciler, kalıcı çağrı geçmişi,
  yedekleme ve geri yükleme.
- Kurulum: `install.sh` (kilitli ve SHA256 doğrulamalı bağımlılıklar, duman testi, otomatik geri dönüş), internetsiz
  paket, systemd birimleri, nginx örneği.
- Deneme: Asterisk deneme santrali ve ARI köprüsü, SIPp senaryoları, yük testi aracı.

[Yayımlanmamış]: https://github.com/Tugbacevikk/sesli-asistan-gateway/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Tugbacevikk/sesli-asistan-gateway/releases/tag/v0.1.0
