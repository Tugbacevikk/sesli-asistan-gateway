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
- Kısa sohbet (ADR-035, `SMALL_TALK=true`): hal hatıra ("günaydın, nasılsınız") LLM sıcak bir cümleyle karşılık
  verir, sonra kiminle görüşmek istendiğini sorar. Varsayılan kapalı; yalın selam yine kodla ve anında.
- Birim yapılı rehber (ADR-032): kayıtlarda isteğe bağlı `birim_yolu` ve `kategori`; birden çok aday seviye seviye
  sorulur ("Jandarma mı, Emniyet mi?" → "Asayiş Şube mi, Trafik Şube mi, Narkotik mi?"), cevap alt birimin adı da
  olabilir ("cinayet"). Sorulan birime verilen cevap artık kurumun santraline aktarılmaz. Demo rehberi
  `data/demo_rehber.json` ve 30 senaryoluk demo seti; üretici betikte eş anlamlılar birime ait.
- Çok turlu makam daraltma (9c552ab): aynı unvanlı ya da aynı birimdeki çok hatta önceki turlarda söylenen birim /
  şube / unvan sözcükleriyle süzülür ("asayiş başkanlığı" → "plan koordinasyon şube" → "idari işlemler astsubayı");
  alt / cihaz hatları ("… Müdürü Shp.") asıl makamın yanında aday sayılmaz. Makam kısaltmaları sözlüğü genişledi,
  sözcük kısaltmaları (`sozcuk_kisaltmalari`: "Asb." → "Astsubayı") ve ek kısaltmalar rehber yüklemede açılır.
  LLM'e makam hatlarında soyadı sormaması söylenir. Arayüzde rehber süzgeci Türkçe karaktersiz ve çok sözcüklü.
- STT'de alçak frekans süzgeci (`stt/denoiser.py`, `STT_HIGHPASS_HZ`, varsayılan 100 Hz; 0: kapalı): klima, şebeke,
  rüzgâr uğultusu. Teşhis kaydı süzgeçten önceki sesi tutar. Etkisi henüz ölçülmedi.
- Rehber aramasında ad + birim birlikte arama: LLM bir yer adını ya da unvan parçasını birim sanıp ayırınca ve
  ayrı arama sonuçsuz kalınca ikisi birlikte aranır; yalnız tek sonuç alınır (düşük güvenliyse teyit).
- Söze girmede ses seviyesi kapısı (ADR-031, `core/level.py`): arayanın kendi seviyesinin 6 dB altındaki arka plan sesi
  asistanın sözünü kesmez (canlı ölçüm: arayan -29 / -31, video -37 / -38 dBFS).
- Rehber yüklemede "Mevcut rehbere ekle" seçeneği: dosyadaki kayıtlar eklenir, hiçbir kayıt silinmez; aynı
  kayıt dosyadakiyle güncellenir (`/v1/admin/directory/preview?mode=merge`). Varsayılan yine "Rehberi değiştir".
- Makam hatlı rehber (ADR-030): rehber yükleme UTF-16 dökümleri ve `access_code` / `sip_name` başlıklarını okur; adı
  olmayan makam hatları (unvan + birim) yeni kayıt türü; kısaltmalar yüklemede açılır (genel sözlük + kurumun
  `ABBREVIATIONS_PATH` dosyası, bilinmeyenler önizlemede); yalnız kod içeren satırlar ve tekrarlar atlanır;
  belgegeçer hatları aktarılamaz. Aynı unvanlı çok hatta "Hangi birimdeki müfettiş?" ve birimle süzme. Makam
  cümlelerinde cins isim eki ("Şube Müdürüne"). Senaryo seti `diyalog_seti_makam.yaml` (sahte rehber).
- Bozuk duyulan birim / konu teyitle (sesli set: "masebeye bağlar mısınız", "çifremi unuttum"); kod doğrudan
  aktarmadan önce sözdeki konu başka birime gidiyorsa ikisi sorulur ("zeynep … faturam" → "Zeynep Arslan mı,
  Muhasebe mi?").
- Sesli regresyon seti: `tools/stt/sesli_set.py` okuma listelerini Piper'la seslendirip G.711 / G.722 hattından
  geçirir, arkaya ikinci konuşan ya da uğultu katar; karsilastir.py + gorev_olc.py ile ölçülür.
- Testler: STT sunucusunun yaşam döngüsü, Whisper yükleme yolları, MCP sunucusunun tüm araçları, açılış (kapsam %92).
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
- Arka plan süzgeci arayanın kısık cevabını atıyordu (canlı deneme 10.11.2026: ilk sözden 17 dB kısık "satış birimi"
  iki kez arka plan sayıldı). Rehberde bir hedefi anan ya da açık soruya cevap veren söz artık kısık da olsa
  dinlenir; ilgisiz kısık sözler eskisi gibi atılır.
- Kişi ve birim hattı birlikte aday olunca "hangisi?" sorusu kodla sorulur ("Ayşe Kaya mı, Satış mı?"); önceden
  LLM'e kalıyor, ~7 sn sürüyor ve bozuk cümle kuruyordu.
- Konu adımına giriş (09.11.2026 eklenen niyet şartı): niyet sözcükleri eksiz eşlendiği için "arızam var", "su arızası
  bildireceğim" ve "ik ile görüşebilir miyim" (D06) konu adımını atlıyordu. Artık yalnız tek sözcüklü, niyet kökü
  ya da birim adı içermeyen kırıntı atlanır; kökler ön ekle eşlenir.
- Testler dosyaları açıkça UTF-8 okur (Windows'ta cp1254 ile okunup başarısız oluyordu).
- Gerçek LLM ölçümünün (06.11.2026, qwen3.5:4b, kamu %90,5) bulguları: aktarılamaz makam ("valiyle") LLM açıkken de
  kodla kurumun genel hattına gider (LLM vali yardımcılarını soruyordu); "şu" katlanınca "su" olduğundan "şu kişiyle
  görüşmek istiyorum" Su ve Kanalizasyon İdaresi'ne aktarılıyordu ("su" tek başına birim sayılmaz); iyelik ekli birim
  adına doğru ek ("İdaresine mi", "İnsan Kaynaklarına mı"; "İdaresi'ye" deniyordu). Ölçüm aracı aktarma / operatör
  eyleminden sonra konuşmayı sürdürmez (gerçek çağrıda sonraki söz asistana ulaşmaz).
- Mesai dışında (`AFTER_HOURS_MODE=assistant`) akışlı cevap kapalı: LLM'in "Sizi operatöre bağlıyorum" cümlesi
  seslendirildikten sonra operatör eylemi "operatörümüz yok"a çevriliyordu; arayan ikisini birden duyuyordu.
- Kimlik doğrulamasız santral API'si artık açılmaz: `API_KEY` boşken Gateway ağa açık adreste (`GATEWAY_API_HOST`
  geri döngü değilse) başlamaz, hata verir (çıkış kodu 2). `GATEWAY_API_HOST` varsayılanı `127.0.0.1` (sunucu şablonu
  `gateway.env` yine `0.0.0.0`; `install.sh` anahtarı üretir). Deneme için açık izin: `ALLOW_INSECURE_API=true`.
  **Yükseltme notu:** elle kurulmuş, `API_KEY`'i boş ve ağa açık Gateway yükseltmeden sonra açılmaz.
- HTTP gövde sınırı (512 KB, rehber dosyası 5 MB) yalnız `Content-Length` başlığına bakıyordu: başlıksız (chunked)
  istekte gövde sınırsız okunuyordu (5 MB'lık chunked `POST /v1/calls` kabul ediliyordu). Sınır artık en dıştaki ASGI
  katmanında **okunan** bayttan uygulanır; aşılınca okuma kesilir, 413 `PAYLOAD_TOO_LARGE` döner.
- Birim belli, kişi belli değil (ADR-031): "muhasebe naşa hanım" (rehberde olmayan ad) birim hattına aktarılmaz,
  hattı olmayan birimde ("personel başkanlığı") "bulamadım" denmez; ikisinde de "Muhasebeden kiminle görüşmek
  istersiniz?" sorulur ve cevap o birimin içinden seçilir (`ASK_PERSON_IN_DEPARTMENT_TEXT`). Tek düşük güvenli aday
  ("ahmet bey" → Mehmet Demir) aktarılmadan teyitle sorulur (D14).
- Canlı prova düzeltmeleri (ADR-031): hedefsiz genel istek ("bilgi almak istiyorum") LLM'e gitmeden konu sorulur;
  çok sözcüklü birimin yalnız sık sözcüğüyle ("bilgi" → Bilgi İşlem) eşleşme kesin sayılmaz; aynı çağrıda ulaşılamayan
  dahiliye arayan yeniden istemedikçe aktarılmaz; LLM sistem istemi ve araçlarla ısıtılır.
- Büyük rehberde hız: rehberden türetilen tablolar rehberin kendisinde önbellekte (3.500 kayıtla söz başına
  0,35–1 sn → 0,04–0,3 sn); `DirectoryIndex.get` sözlükle.
- Genel konu listesine "servis kaydı", "arıza kaydı" (→ teknik destek). `gorev_olc.py`: teyitle gidecek hedefe
  doğrudan gitmek "doğru" sayılır; kaçırmalar da sonuçla birlikte listelenir.
- Karışık sözde (arkada ikinci konuşan) doğrudan eylem yok, ADR-029: arka plandan tek bir ad ya da birim + arayanın
  konusu ("satış mı faturamla ilgili bir sorun var" → "Satış mı, Muhasebe mi?"), soyadın önünde başka ad ("murat
  çelik" → Burak Çelik: teyit), aktarılamayan kişi + birim, operatör isteği + bozuk birim adı. Kural modunda yalnız adla
  bulunan kişi teyitle (ADR-025 ile aynı). Konu listesi Gateway'de de yüklenir: MCP'ye ulaşılamazsa konu adımı yerelde.
  `gorev_olc.py` üretimdeki akışla ölçer. Sesli setin 36 yanlış eylemli metni yeniden oynatıldığında 6 kaldı. D135–D138.
- Sözde birden çok hedef varsa aktarılmaz, sorulur (ölçüm 05.11.2026, sesli set: arkada ikinci konuşan 10 dB kısıkken
  STT iki sözü karıştırıyor, hızlı yol ilk bulduğuna aktarıyordu — 80 kayıtta 18 yanlış eylem). İki kişi, iki birim
  hattı, dahili + başka kişi → "X mı, Y mi?"; operatör / kapatma isteği yanında bir hedef → teyit. D131–D134.
- STT sunucusu sinyal testi gerçek SIGTERM göndermiyor (ağır yükte test sürecini öldürebiliyordu).
- `mcp_person_lookups` metriği konu adımıyla aynı: bulundu / **yok** / hata; MCP düşünce yerelde bulunan kişi "bulundu"
  sayılmaz. Ölü kod temizliği (kullanılmayan `nwords`, `stream_time`, `RTP_CLOCK`); dialog'daki yinelenen harf
  katlama ve `_skeleton` adı (people.py'dekiyle karışıyordu → `_vowel_mask`).
- Çok düşük güvenli sözle çağrı kapanmaz (canlı 05.11.2026: arkadaki boğuk ses "kapatıya geç" (0,31) duyuldu ve
  çağrı kapandı): güven tekrar eşiğinin altındaysa yalnız kurallı kapatma sözü ("kapatın", "sonlandırmak
  istiyorum") kapatır. Söze giren gürültü yalnız dolgu sesi ("şey") verdiyse de yarıda kalan cümle yinelenir. D130.
- LLM düşük güvenli sözde geçmeyen kişiyi öneremez (canlı 05.11.2026): rehberde olmayan "Tuğba Çevik" STT'de
  "çokağa çeviriyle" (güven 0,6) duyuldu, LLM "Kaya" diye arayıp "Ayşe Kaya'ya mı bağlamamı istersiniz?" sordurdu.
  Hedefin adı / soyadı / unvanı / birimi / dahilisi arayanın sözünde benzer biçimde (≥ 75) geçmiyorsa teyit de
  sorulmaz, "bulamadım" denir. Senaryo D129.
- Ortam sesi "Sizi anlayamadım" döngüsü (canlı 04.11.2026): söze girme anlaşılmadığında (boş ya da çok düşük güvenli
  sonuç) asistan "anlayamadım" yerine yarıda kalan cümlesini yeniden söyler (üst üste en çok 2 kez, deneme hakkı
  yanmaz); güveni 0,2'nin altında 1–2 harflik sonuçlar ("b") cevaplanmaz.
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
- `CallSession` karışım sınıflarına bölündü (kod aynen taşındı): seslendirme `core/call/speaking.py`, santral
  eylemleri `core/call/pbx_actions.py`; olay tipi `core/call/models.Event`. `session.py` 600 → 400 satır.
- `GatewayRuntime`: santral ve arayüz çağrısının oturumu tek yerde kurulur (`_session`; önceden iki kopya), LLM
  ısıtma ayrı işlev (`warm_llm`).
- Rehber dosyasına yazma, yedek ve geri alma bir arayüzün arkasında (`ports.DirectoryStore`,
  `adapters/directory/json_file_store.py`); rehber yükleme akışı dosya sistemini bilmez. Dosya adları aynı.
- Ayar servisi çalışan sistemin iç alanlarını değiştirmez; dar giriş noktalarını kullanır:
  `DialogAgent.set_texts`, `GatewayRuntime.set_office_rules`, `GatewayRuntime.reload_prompts`
  (`prompts_missing` artık açık alan).
- Yerel ayarlar tipli (`local_settings.LocalSettings`, `LocalContact`): ayar servisi serbest sözlük yerine bunu
  taşır; ayar dosyasının biçimi aynı (yalnız farklar). Mesai / tatil / mesai dışı davranışın "yerel yoksa ayar
  dosyasındaki" kuralı tek yerde.
- Dosyalar sorumluluğa göre bölündü (kod aynen taşındı, davranış ve içe aktarma yolları aynı): `core/people/`,
  `core/call/` (models, session), `directory_import/` (common, table, rows, compare), `directory_upload.py`,
  `api/web_call.py`.
- Çağrı olay adları sabit (`core.models.CallEvent`); canlı izleme ve metrikler metin yerine sabiti kullanır
  (adlar değişmedi, arayüz aynı). Eksik dönüş tipleri yazıldı (`_thinking` genel tipli: sarılan işin sonucunu döner).
- `core/dialog.py` (2.000 satır) konu konu modüllere bölündü (`core/dialog/`, ADR-034); davranış aynı.
- Kuruma özgü STT düzeltmeleri ve yer adları ürün kodundan çıktı (ADR-033): kurum dosyasında (ABBREVIATIONS_PATH)
  `stt_duzeltmeleri` ve `ad_olmayan_sozcukler`. "bakanlığı ≈ başkanlığı" genel eşitliği kaldırıldı.
- LLM rehberde bulunanı anmadan kendi sorusunu sorarsa deneme sayılır (sayaç her turda sıfırlanıp operatör teklifine
  ulaşılamıyordu, canlı 09.11.2026). Yeni unvan kökleri: işletmen, şoför, teknisyen, görevli, kontrolör …
- README durum tablosu güncellendi.
- STT cümle ön payı 350 → 500 ms (`STT_PRE_ROLL_MS`, artık ayardan): ilk hece kesilmesin. Testler ön paya göre.
- Tarayıcı denemesinde tarayıcının gürültü bastırması açık: deneme sesi telefon hattından biraz daha farklı.
- G.722 santral ekibince onaylandı (05.11.2026): önerilen yapılandırma `codec: "g722"` (ADR-027).
- Diyalog akışı adım zincirine bölündü (`dialog.STEPS`): en karmaşık işlev 63'ten 24'e indi, adımların sırası tek
  listede ve sıra kuralları testte. Söz kalıpları ve Türkçe ekler `core/phrases.py`'de. Eşikler tek yerde:
  `core/thresholds.py` (tasarim.md Ek E). Rehber dosyası okuyucu (57 → 15) ve ayar doğrulaması (37 → 14) küçük
  işlevlere bölündü. Davranış aynı; yalnız reddedilen rehber satırı için artık takma ad uyarısı yazılmaz.
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
