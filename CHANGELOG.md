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
- İkinci STT motoru (ADR-021): `STT_ENGINE=sherpa` ile akışlı Zipformer transducer (sherpa-onnx, CPU;
  mihuai/turkish-stt-66m-v2).
- Yerel LLM + MCP deneyi: `tools/mcp/` (rehber araçları MCP sunucusu, Ollama istemci döngüsü, 8 senaryo).
- İzleme sekmesinde canlı çağrılar: santral çağrıları dahil, süren her çağrının konuşması (arayan / asistan,
  aktarma, ilk ses süresi) anında (`WS /v1/ui/canli`).
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
- STT ayarlarını görevle ölçme: `tools/stt/gorev_olc.py` (doğru iş / teyit / yanlış eylem), okuma kaydı için
  santralde 600 dahilisi ve `tools/stt/okuma_bol.py`; `karsilastir.py` `--rehber`, `--beam`, `--aralik`, güven.
  Ad ipuçları varsayılan kapalı (`STT_HOTWORDS=false`): bozuk seste duyulmayan rehber adlarını yazdırıyordu.
- `tools/stt/vad_incele.py` ve teşhis kaydında çağrının bütün sesi: VAD'in kaçırdığı konuşmayı seviye ve eşik eşik gösterir.
- `tools/stt/karsilastir.py`: STT modellerini (Whisper, sherpa-onnx akışlı transducer) aynı kayıtlarla, ipuçlu /
  ipucusuz karşılaştırır (WER, CER, RTF); model kataloğunda yalnız değerlendirme için large-v3-turbo ve mihu.
- Sürüm yönetimi: bu dosya, `tools/kurulum/surum.py`, etiketten GitHub sürümü oluşturan CI işi; sürüm sağlık
  yanıtında (`version`).

### Düzeltildi
- Yanlış kişiye aktarma koruması: STT güveni düşükse (`CONFIRM_BELOW`) aktarmadan önce teyit sorusu; tam adın
  yanında başka kişinin soyadı geçerse kodla "hangisi?"; "hiçbiri" kodla "bulamadım". İzleme'de STT güveni görünür.
- "operatör" STT ipucunda her zaman; operatör isteği LLM beklemeden.
- STT: Whisper'ın ipucu listesinin devamını yazması ("ayşe yılmaz" → "ayşe yılmaz zeynep arslan") ayıklanır.
- Sözde birden çok kişinin tam adı geçince "bulunamadı" yerine kodla "hangisi?" sorulur.
- Arayan görüşmeyi bitirmek isteyince ("sonlandırmak istiyorum", "kapatabilirsiniz", "vazgeçtim") vedalaşıp kapatılır.
- Asistanın sözü kesilerek söylenen ve anlaşılamayan söz deneme hakkından düşmez (operatöre gönderilmez).
- VAD eşiği 0,5 → 0,35 (`STT_VAD_THRESHOLD`): hoparlör / yankı gidericiyle gelen kısa cevaplar kaçıyordu.
- STT (sherpa / mihu): güven Whisper ölçeğine çevrilir; Gateway eşikleri motordan bağımsız (okuma ölçümüyle).
- STT (sherpa): cümlenin ilk kelimesi düşmesin diye sesin önüne 0,3 sn sessizlik.
- STT teşhis kaydı: çağrı kimliğinde nokta varsa her cümle aynı dosyanın üstüne yazılıyordu.
- LLM düşük STT güvenli sözle aktarmak isterse aktarmaz, kodla teyit sorar (STT "ateşe girmem", güven 0,31 → aktarmıştı).
- "Hangisi?" sorusuna bozuk duyulan cevap bir adayın soyadına açıkça yakınsa ("her şey uymaz" → Yılmaz) teyitle sorulur.
- STT güveni çok düşük söz (`REPEAT_BELOW`, 0,5) LLM'e gitmez, kodla tekrar istenir (LLM anlamsız sözle
  aktarıyor / operatöre bağlıyordu).
- STT güveni karar için yuvarlanmaz (0,495 → 0,5 sayılıp tekrar eşiğini geçiyordu).
- Yazıyla söylenen dahilide çekim eki: "iki yüz on dörde" 210 değil 214 (yanlış birime aktarma).
- Teyit sorusunda soru eki ünlü uyumuna uyar ("Yılmaz'a mı"; `{mi}` yer tutucusu).

### Değişti
- **Varsayılan STT motoru mihu** (`STT_ENGINE=sherpa`, mihuai/turkish-stt-66m-v2, CPU): okuma ölçümünde hatasız,
  en düşük kelime hatası, gerçek zamanın ~14 katı hızlı; STT için GPU gerekmez (kurulum GPU'yu yalnız
  `STT_ENGINE=whisper` ise denetler). Whisper GPU'lu yedek olarak kalır.
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
