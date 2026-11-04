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
- Geniş bant ses (ADR-027): santral `codec: "g722"` gönderebilir; Gateway G.722'yi çözüp STT'ye 16 kHz verir (hatta
  kaybolan s / ş / f sesleri). Deneme santrali: `allow=g722,…`, köprü `--codec g722`. Yeni bağımlılık `G722` (kamu malı).
- Ses benzerliğiyle ad (ADR-026): STT adı bozduğunda ("bırakçayı" → Burak Çelik, "ağlayamaz" → Ayşe Yılmaz) rehberdeki
  en benzer kişi teyitle sorulur; LLM'den hemen önce, operatör kuralından sonra.
- STT hışırtılı ses ölçümü: `tests/eval/okuma_hisirti.tsv` (s / ş / f / z ile başlayan anahtar sözcükler) ve
  `tools/stt/mikrofon_kaydet.py` (16 kHz mikrofon kaydı; telefon hattıyla karşılaştırma).
- MCP adımında kişi çözme (ADR-025, `kisi_coz`): unvan ("il jandarma komutanı", "Nilüfer kaymakamı"), birim + ad
  ("muhasebeden Ayşe Hanım"), ad + hitap / rütbe ("Mehmet Bey", "Mehmet Albay") LLM'siz çözülür; MCP düşerse Gateway
  aynı kuralları kendisi çalıştırır. Kamu unvanları ve rütbeler tanınır; "Komutanlığı / Müdürlüğü" kurum sayılır.
  Sentetik kamu rehberi ve 21 senaryoluk kamu seti (`--rehber`): %66,7 → %100; şirket seti %96,7 → %99,2.
- Sistem istemi ölçülerek değiştirilebilir (ADR-024): şablon dosyası (`LLM_PROMPT_PATH`, açılışta doğrulanır), isteme
  kurumun birim listesi (`LLM_PROMPT_DEPARTMENTS`), örnek konuşmalar (`LLM_FEW_SHOT`); değerlendirmede `--istem`,
  `--birim-listesi`, `--ornekler` ve cevapta yasak metin denetimi. İsteme saldırılara karşı kural; diyalog setine
  6 saldırı senaryosu (120 senaryo). Tasarım Ek B'deki istem koddan üretilir, test fark yakalar.
- Akış rehber → MCP → LLM (ADR-023, `MCP_STEP`): rehber çözemeyince LLM'den önce yerel MCP sunucusuna konu sorulur
  (`konu_ara`: "faturamla ilgili" → Muhasebe, "arızam var" → Teknik Destek). Ürünle genel konu listesi gelir
  (şirket ve kamu konuları; birimler kurumun rehberine kendiliğinden eşlenir), kurum kendi listesini verebilir
  (`MCP_TOPICS_PATH`). Kural modunda diyalog seti %96,5 → %99,1. Kurulum şablonunda açık; metrik
  `mcp_topic_lookups_total`, değerlendirmede `--mcp-adimi`.
- Rehber MCP sunucusu (ADR-022): `python -m sesli_asistan.gateway.rehber_mcp` / `sesli-mcp.service`, yalnız okuyan
  araçlar (`rehber_ara`, `birimler`, `mesai_durumu`), varsayılan yalnız 127.0.0.1:8765. `LLM_TOOLS=mcp` ile LLM'in
  rehber araması bu sunucuda; aktarma Gateway'de kalır, sunucu ulaşılamazsa arama yerelde (`MCP_TIMEOUT_S`).
  Metrik `mcp_tool_calls_total`, değerlendirmede `--araclar mcp`. Varsayılan `yerel` (davranış değişmez).
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
- Mesai sorusu ("mesai saatleri ne", "saat kaça kadar açıksınız") kodla cevaplanır: OFFICE_HOURS okunuşuyla
  ("hafta içi sekiz otuz ile on sekiz arası"), açık soru yinelenir, deneme sayılmaz. LLM, arayan istemedikçe operatöre
  bağlayamaz (kod denetler: operatör isteği ya da teklife "evet"). Canlı: aktarma başarısızlığından sonra mesai
  sorusu operatöre bağlanmıştı.
- Dolgu sesleri ("eee", "ıı", "hmm", "şey") sözden atılır; yalnız dolgu varsa LLM'e gidilmez, açık soru yinelenir ya da
  "anlayamadım" denir, deneme sayılmaz (canlı: "eee merhaba" ve "e" LLM'e gidiyordu).
- Arayan bir numara söylediyse LLM başka bir dahiliye (kişinin adı geçmiyorsa) aktaramaz, teyit sorulur (ölçüm:
  örnekli istemde "yüz numaraya bağlayın" → 214'e aktarılmıştı).
- MCP sunucusu düşükken LLM'in rehber araması 1 sn zaman aşımını beklemeden yerelde yapılır (ölçüm: her arama ~1 sn
  gecikiyordu).
- Yanlış kişiye aktarma koruması: STT güveni düşükse (`CONFIRM_BELOW`) aktarmadan önce teyit sorusu; tam adın
  yanında başka kişinin soyadı geçerse kodla "hangisi?"; "hiçbiri" kodla "bulamadım". İzleme'de STT güveni görünür.
- "operatör" STT ipucunda her zaman; operatör isteği LLM beklemeden.
- STT: Whisper'ın ipucu listesinin devamını yazması ("ayşe yılmaz" → "ayşe yılmaz zeynep arslan") ayıklanır.
- Sözde birden çok kişinin tam adı geçince "bulunamadı" yerine kodla "hangisi?" sorulur.
- Arayan görüşmeyi bitirmek isteyince ("sonlandırmak istiyorum", "kapatabilirsiniz", "vazgeçtim") vedalaşıp kapatılır.
- Asistanın sözü kesilerek söylenen ve anlaşılamayan söz deneme hakkından düşmez (operatöre gönderilmez).
- VAD eşiği 0,5 → 0,3 (`STT_VAD_THRESHOLD`): hoparlör / yankı gidericiyle ve sorunun hemen ardından gelen kısa
  cevaplar kaçıyordu.
- STT (sherpa / mihu): güven Whisper ölçeğine çevrilir; Gateway eşikleri motordan bağımsız (okuma ölçümüyle).
- STT (sherpa): cümlenin ilk kelimesi düşmesin diye sesin önüne 0,3 sn sessizlik.
- STT teşhis kaydı: çağrı kimliğinde nokta varsa her cümle aynı dosyanın üstüne yazılıyordu.
- LLM düşük STT güvenli sözle aktarmak isterse aktarmaz, kodla teyit sorar (STT "ateşe girmem", güven 0,31 → aktarmıştı).
- "Hangisi?" sorusuna bozuk duyulan cevap bir adayın soyadına açıkça yakınsa ("her şey uymaz" → Yılmaz) teyitle sorulur.
- STT güveni çok düşük söz (`REPEAT_BELOW`, 0,5) LLM'e gitmez, kodla tekrar istenir (LLM anlamsız sözle
  aktarıyor / operatöre bağlıyordu).
- STT güveni karar için yuvarlanmaz (0,495 → 0,5 sayılıp tekrar eşiğini geçiyordu).
- Yazıyla söylenen dahilide çekim eki: "iki yüz on dörde" 210 değil 214 (yanlış birime aktarma).
- Rehberde karşılığı olmayan tek bir hedefe bağlanmak istenince ("afiyete bağlanmak istiyorum": bozuk duyulan
  "operatöre") LLM yerine kodla "Sizi operatöre mi bağlamamı istersiniz?" (`CONFIRM_OPERATOR_TEXT`).
- "Hangisi?" sorusunun anlaşılmayan cevabında soru kodla yinelenir ve açık kalır; soyadıyla aynı sesle biten
  bozuk cevap ("yaramaz" → Yılmaz) teyitle sorulur. Harf harf duyulan ("o pt'ye") ya da "operatör"e yakın
  ("aparator") söz operatör teyidine gider.
- Aynı "hangisi?" sorusu yeniden sorulunca deneme sayılır (sayaç sıfırlandığı için sonsuz döngü vardı); ünlü
  uyumuyla bozulan soyad ("yüzmez" → Yılmaz) eşlenir; ortak ad seçimde ayırt edici sayılmaz.
- Kısa cevaplar ("evet") öksürük sayılıp atılmaz: en az sesli süre 0,3 → 0,15 sn (`STT_MIN_VOICED_S`); sorunun
  hemen ardından verilen "evet" kayboluyor, asistan "duyamıyorum" diyordu.
- Soru açıkken (teyit, operatör, "hangisi?") arayan susarsa "Sizi duyamadım." + aynı soru yeniden sorulur.
- Hazır anonslarda "ilk ses" süresi çalma başlarken ölçülür (anonsun süresi gecikme gibi görünüyordu).
- "Bir insanla görüşmek istiyorum" İnsan Kaynakları'na, "sistem … yönetici" Sistem Yöneticisi'ne aktarılmaz
  (tek söz unvan / birim sözüyle kişi seçmez); "insanla", "gerçek biriyle", "müşteri temsilcisi" operatör isteği.
- Tam ad başka bir birimle söylenirse ("satıştan Burak Çelik") aktarılmaz, teyit sorulur.
- Diyalog seti 110 senaryo (`tests/eval/diyalog.py --kural`: LLM'siz koşu; adımlarda STT güveni; `kapat` beklentisi).
- Yalnız selam ("selam", "merhaba", "alo") "bulamadım" almaz, deneme sayılmaz: "Merhaba, kiminle görüşmek
  istersiniz?" (`GREETING_REPLY_TEXT`). LLM cevabı en çok iki cümle.
- Söylenen dahili numara ("iki yüz on dört", "214") LLM açıkken de beklemeden aktarılır.
- Teyit sorusunda soru eki ünlü uyumuna uyar ("Yılmaz'a mı"; `{mi}` yer tutucusu).

### Değişti
- Konudan bulunan birime aktarmadan önce teyit sorulur ("Muhasebe'ye mi bağlamamı istersiniz?"; `TOPIC_CONFIRM`).
  Öneriye "hayır" denince "bulamadım" değil "Peki. Kiminle ya da hangi birimle görüşmek istersiniz?" (`DECLINED_TEXT`);
  STT güveni düşük teyidinde "hayır" eskisi gibi adı yeniden ister.
- Rehber yenilemesi değişiklik yoksa loga yazılmaz (yalnız DEBUG düzeyinde).
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
