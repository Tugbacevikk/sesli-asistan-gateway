"""Söz kalıpları ve Türkçe ekler: arayanın sözünde ne istendiği (operatör, kapatma, mesai, selam, dolgu, numara)
ve asistanın cümlelerindeki ekler (yönelme, ayrılma, soru eki). Durumsuz, saf işlevler; diyalog adımları
(`dialog.py`) bunları kullanır. Sözcük kümeleri **katlanmış** (küçük harf, ç→c, ğ→g, ı→i, ö→o, ş→s, ü→u).
"""
from __future__ import annotations

import re
from typing import Any

from sesli_asistan.libs.text.normalizer import TR_FOLD, fold_tr_words, turkish_lower

from .search import search_key, similarity, strip_suffix
from .thresholds import OPERATOR_LIKE, OPERATOR_MAYBE

# Bu sözcüklerden biri geçerse hızlı yol kapalı: arayan düzeltiyor, reddediyor ya da başka bir şey istiyor olabilir
_FAST_BLOCK = {"degil", "hayir", "yok", "istemiyorum", "olmasin", "baska", "yerine", "operator", "iptal", "vazgectim",
               "ama", "veya", "ya", "kim", "hangi", "nerede", "numara", "numarasi", "dahili",
               "ne", "neden", "niye", "nasil", "alaka", "alakasi", "yanlis", "diyorsun", "dedin", "mu"}



_BACK, _FRONT = set("aıou"), set("eiöü")


def dative(name: str) -> str:
    """Özel ada Türkçe yönelme hâli eki (ünlü uyumu): Kartal → Kartal'a, Bursa → Bursa'ya, Deniz → Deniz'e."""
    word = (name or "").strip()
    vowels = [ch for ch in word.lower() if ch in _BACK | _FRONT]
    if not vowels:
        return word
    suffix = "a" if vowels[-1] in _BACK else "e"
    if word[-1].lower() in _BACK | _FRONT:
        suffix = "y" + suffix
    return f"{word}'{suffix}"



# Dolgu sesleri (katlanmış): "eee merhaba", "ıı ayşe hanım", tek başına "e" — sözden atılır. "hı" yok: "hı hı" = evet.
# Canlı deneme 04.11.2026: "eee merhaba" selam sayılmayıp LLM'e gitti (4,3 sn, "yine bulamadım"); "e" de LLM'e gitti.
_FILLERS = {"e", "ee", "eee", "eeee", "eeeee", "ii", "iii", "iiii", "mm", "mmm", "hm", "hmm", "hmmm", "eh", "ehh",
            "ehm", "eem", "aa", "aaa", "sey", "seyy"}


def common_dative(phrase: str) -> str:
    """Cins isme yönelme eki, kesmesiz (makam hattı, ADR-030): "Şube Müdürü" → "Şube Müdürüne", "Salon Sorumlusu" →
    "Salon Sorumlusuna" (tamlamada iyelikten sonra -n-), "Nizamiye" → "Nizamiyeye", "Avukat" → "Avukata"."""
    w = phrase.strip()
    vowels = [ch for ch in turkish_lower(w) if ch in _BACK | _FRONT]
    if not vowels:
        return w
    suffix = "a" if vowels[-1] in _BACK else "e"
    last = turkish_lower(w)[-1]
    if last in "ıiuü" and " " in w:
        return f"{w}n{suffix}"
    if last in _BACK | _FRONT:
        return f"{w}y{suffix}"
    return w + suffix


def contact_dative(c: Any) -> str:
    """Kaydın söylenen adı yönelme ekiyle: kişi ve birim özel ad ("Ayşe Yılmaz'a", "Muhasebe'ye"), makam cins isim.
    İyelik ekiyle biten çok sözcüklü birim adı tamlamadır: -n- ile ("Su ve Kanalizasyon İdaresine", "İnsan
    Kaynaklarına"; "İdaresi'ye" yanlış okunuyordu, 06.11.2026)."""
    name = c.full_name if c.kind != "department" else c.department
    if c.kind == "role" or (c.kind == "department" and " " in name.strip()
                            and turkish_lower(name.strip())[-1:] in ("ı", "i", "u", "ü")):
        return common_dative(name)
    return dative(name)


def asks_general_help(text: str) -> bool:
    """Hedefsiz genel istek: "bilgi almak istiyorum", "bir sorum var", "bir şey soracaktım", "yardım istiyorum"
    (canlı 05.11.2026: "bilgi almak istiyorum" Bilgi İşlem'e aktarıldı). Kişi / birim / konu ayrıca denetlenir."""
    w = _folded_words(text)
    joined = " ".join(w)
    if any(a == "bilgi" and b.startswith("al") for a, b in zip(w, w[1:], strict=False)):
        return True
    # "şey" dolgu sesi olarak atıldığı için "bir şey soracaktım" buraya "bir soracaktım" diye gelir
    return bool(re.search(r"\b(bir )?(sorum|soru) (var|olacak|soracaktim|sormak)|\b(soracaktim|soracagim|"
                          r"danisacaktim)\b|\bsormak istiyorum|\byardim (istiyorum|almak|lazim)", joined))


def strip_fillers(text: str) -> str:
    """Sözden dolgu sesleri atılır (noktalama ve sözcük sırası korunur)."""
    return " ".join(w for w in text.split() if not (_folded_words(w) and _folded_words(w)[0] in _FILLERS
                                                     and len(_folded_words(w)) == 1))



# arayan görüşmeyi bitirmek istiyor (kök başları; "kapatmayın" gibi olumsuzlar hariç)
_HANGUP_STEMS = ("sonlandir", "kapat", "vazgec", "gorusuruz", "hoscakal", "hoscak")
_YES = {"evet", "evt", "aynen", "dogru", "tamam", "olur", "lutfen", "he", "hi", "kesinlikle", "baglayin",
        "baglayabilirsiniz"}
_NEGATION = {"degil", "yok", "istemiyorum", "istemem", "olmasin", "gerekmez", "hayir"}
# tek sözcüklü sözde konu adımına girmek için niyet kökleri (katlanmış, ön ekle eşlenir: "arizam", "gorusebilir")
_TOPIC_INTENT = ("icin", "ilgili", "sorun", "problem", "hakkinda", "konu", "ariza", "sikayet", "bagla", "gorus",
                 "aktar", "yardim", "iste", "isti", "bilgi", "fatura", "odeme", "sifre", "siparis", "basvuru")
_NEGATIVE = re.compile(r"(kapatma|sonlandirma|vazgecme)(y|z|d|n|s|$)")
# kurallı kapatma fiilleri (katlanmış): düşük güvende yalnız bunlar
_HANGUP_FORM = re.compile(r"(kapat|sonlandir)(|in|iniz|mak|ir|irsiniz|iyorum|abilir\w*|ayim|alim|sana)"
                          r"|vazgec(tim|tik|iyorum)|gorusuruz|hoscakal(in)?")
_HANGUP_PHRASES = ("gorusmeyi bitir", "hosca kal", "gerek yok", "bu kadar yeter", "baska bir sey yok",
                   "kimseyle gorusmeyecegim", "kimseyle gorusmek istemiyorum")


_folded_words = fold_tr_words      # kısa ad (diyalog modüllerinde çok kullanılır)


_GREETING_WORDS = {"selam", "selamlar", "merhaba", "merhabalar", "alo", "iyi", "gunler", "gunaydin", "aksamlar",
                   "selamun", "aleykum", "aleykumselam", "selamunaleykum", "kolay", "gelsin", "hayirli", "isler",
                   "nasilsiniz", "nasilsin", "efendim", "evet", "buyurun", "hosbulduk", "tesekkurler", "sagolun",
                   "tesekkur", "ederim", "duyuyor", "musunuz", "beni", "sesim", "geliyor", "mu",
                   "hos", "gel", "geldin", "geldiniz", "hosgeldin", "hosgeldiniz"}


_HOURS_WORDS = {"acik", "aciliyor", "acilir", "aciyorsunuz", "kapaniyor", "kapanir", "kapatiyorsunuz", "kadar",
                "kacta", "kaca", "kac", "calisiyorsunuz", "calisiyor"}


def asks_office_hours(text: str) -> bool:
    """Arayan mesai / çalışma saatini soruyor mu: "mesai saatleri ne", "saat kaça kadar açıksınız", "kaçta açılıyor",
    "çalışma saatleriniz", "açık mısınız"."""
    words = _folded_words(text)
    joined = " ".join(words)
    if "mesai" in joined or "calisma saat" in joined:
        return True
    if any(w.startswith("saat") for w in words) and set(words) & _HOURS_WORDS:
        return True
    return bool(re.search(r"\b(kacta|kaca kadar) (aciliyor|acilir|kapaniyor|kapanir|aciksiniz|acik)", joined)
                or re.search(r"\bacik (misiniz|mi)\b", joined))


# "hoş geldiniz": asistanın kendi karşılaması mikrofondan geri geliyor ("denem'e hoş geldin", "benim hoş gel"; çağrı
# geçmişi 05.11.2026: 5 tur LLM'e gidip 1,5–12 sn bekletti, sonunda "bulamadım"); selam gibi kodla, anında
_GREETING_CORE = {"selam", "selamlar", "merhaba", "merhabalar", "alo", "gunaydin", "aksamlar", "selamun", "aleykum",
                  "aleykumselam", "selamunaleykum", "gunler", "gelsin", "nasilsiniz", "nasilsin", "hos", "geldin",
                  "geldiniz", "hosgeldin", "hosgeldiniz"}


_HOW_ARE_YOU = {"nasilsin", "nasilsiniz", "naber", "nabersin", "nasil", "nasilsinizdir", "iyimisin", "iyimisiniz",
                "keyifler", "ne", "haber"}


_ECHO = (("gunaydin", "Günaydın"), ("iyi aksamlar", "İyi akşamlar"), ("hayirli aksamlar", "Hayırlı akşamlar"),
         ("iyi gunler", "İyi günler"), ("hayirli gunler", "Hayırlı günler"), ("selamun aleykum", "Aleykümselam"),
         ("selamunaleykum", "Aleykümselam"))


def greeting_echo(text: str) -> str | None:
    """Arayanın selamına aynı sözle karşılık ("günaydın" → "Günaydın"); bilinen bir selam yoksa None."""
    said = " ".join(_folded_words(text))
    return next((reply for key, reply in _ECHO if key in said), None)


_ANYONE = ("musait", "herhangi", "fark etmez", "farketmez", "kim olursa", "hangisi olursa", "kim varsa", "biri olur",
           "hepsi olur", "kim bakiyorsa", "ilgilenen")


def wants_anyone(text: str) -> bool:
    """"Hangisi?" sorusuna "kim müsaitse", "fark etmez", "herhangi biri": sorulanlardan biri yeter (deneme 10.11.2026:
    "Ar-Ge'den kiminle?" → "kim müsaitse" anlaşılmadı, söz LLM'e gitti, isimler sayıldı)."""
    said = " ".join(_folded_words(text))
    return any(k in said for k in _ANYONE)


def is_small_talk(text: str) -> bool:
    """Selamın ötesinde hal hatır ("günaydın, nasılsınız", "naber"): sohbet açık iken LLM karşılık verir."""
    w = set(_folded_words(text))
    return bool(w & (_HOW_ARE_YOU - {"ne", "haber"})) or {"ne", "haber"} <= w


def is_greeting_only(text: str, noise: int = 0) -> bool:
    """Söz yalnız selam / hal hatır / "alo, duyuyor musunuz" mu (bir istek yok)? En az bir selam sözü olmalı.
    Sonu kesilmiş selam ("merhab") selam sayılır. `noise`: tanınmayan en çok kaç sözcüğe izin verilir (STT kırıntısı:
    "bakalımızı merhaba iyi günler", canlı 09.11.2026); çağıran sözün rehberde bir hedef içermediğini ayrıca bakar.
    Kesmeden sonraki ek atılır: "denem'e" iki tanınmayan sözcük ("denem", "e") sayılıyordu."""
    raw = _folded_words(re.sub(r"['’]\w*", "", text))

    def known(w: str) -> bool:
        return w in _GREETING_WORDS or (len(w) >= 4 and any(g.startswith(w) for g in _GREETING_CORE))

    core = any(w in _GREETING_CORE or (len(w) >= 5 and any(g.startswith(w) for g in _GREETING_CORE)) for w in raw)
    return bool(raw) and core and sum(not known(w) for w in raw) <= noise


def _vowel_mask(word: str) -> str:
    """Ünlüleri tek işarete indirir ("yılmaz", "yüzmez" → "y?lm?z"): ünlü uyumuyla bozulan sonlar için."""
    return re.sub(r"[aeıioöuü]", "?", word)


_CONNECT_VERB = re.compile(r"^(bagla|baglan|aktar)")
_DATIVE_END = re.compile(r"y?[ea]$")


def unknown_target(text: str) -> str | None:
    """Sözde tek bir (dolgu dışı) hedef kelime + bağlanma fiili var mı: "afiyete bağlanmak istiyorum" → "afiyete".
    Hedefin rehberde olup olmadığına çağıran bakar. İki kelimelik hedef (ad soyad: "Selin Aydın'a") ya da kısa söz
    ("ona") sayılmaz."""
    raw = _folded_words(text)
    idx = next((i for i, w in enumerate(raw) if _CONNECT_VERB.match(w)), None)
    if not idx:
        return None
    before: list[str] = []
    for w in raw[:idx]:                        # kesme işaretiyle ayrılan ek ("Hakan'a" → hakan + a) öncekine katılır
        if before and len(w) <= 3 and _DATIVE_END.fullmatch(w):
            before[-1] += w
        elif w not in _FILLER:
            before.append(w)
    if len(before) > 1 and all(len(w) <= 2 for w in before[:-1]):
        # harf harf bölünmüş söz: "o pt'ye", "o p t'ye" (canlı 03.11.2026, mihu "operatöre"yi böyle duydu)
        before = ["".join(before)]
    if len(before) != 1 or len(before[0]) < 4 or not _DATIVE_END.search(before[0]):
        return None
    return before[0]


def maybe_operator(text: str) -> bool:
    """"operatör"e yakın ama `asks_operator`'ın doğrudan aktarma eşiğine (85) varmayan söz ("aparator", 75): teyitle
    sorulur. Canlı deneme 03.11.2026: "aparator ev bayranmak istiyorum" LLM'e gitmiş, "bulamadım" denmişti."""
    return any(len(w) >= 6 and max(similarity(w, k) for k in ("operator", "operatore", "operatorle")) >= OPERATOR_MAYBE
               for w in _folded_words(text))


def asks_operator(text: str) -> bool:
    """Sözde operatör isteği var mı: "operatöre bağlayın", "santral", "bir yetkiliyle"; STT'nin harf hatalarına
    karşı "operatör"e çok benzeyen kelimeler de ("oparatöre", "operetör"). Olumsuzsa ("operatöre gerek yok",
    "operatör istemiyorum") hayır."""
    raw = _folded_words(text)
    if not raw or set(raw) & _NEGATION:
        return False
    if ({strip_suffix(w) for w in raw} | set(raw)) & _OPERATOR_WORDS:
        return True
    joined = f" {' '.join(raw)} "
    if any(f" {p} " in joined for p in _OPERATOR_PHRASES):
        return True
    return any(len(w) >= 7 and max(similarity(w, k) for k in ("operator", "operatore", "operatorle")) >= OPERATOR_LIKE
               for w in raw)


def wants_hangup(text: str, strict: bool = False) -> bool:
    """"görüşmeyi sonlandırmak istiyorum", "kapatabilirsiniz", "vazgeçtim", "teşekkürler görüşürüz". `strict`
    (STT güveni çok düşük): kök yalnız kurallı bir fiil ekiyle ("kapatın", "kapatabilirsiniz"; "kapatıya" değil)."""
    t = " ".join(re.sub(r"[^\w\s]", " ", turkish_lower(text).translate(TR_FOLD)).split())
    words = t.split()
    for w in words:
        # olumsuz: "kapatma", "kapatmayın", "sonlandırmadan" (ama "kapatmak", "sonlandırmak" olumlu)
        if w.startswith(_HANGUP_STEMS) and not _NEGATIVE.match(w) and (not strict or _HANGUP_FORM.fullmatch(w)):
            return True
    return any(f" {p}" in f" {t}" for p in _HANGUP_PHRASES)
# "bir insanla görüşmek istiyorum", "gerçek biriyle": operatör isteği (diyalog seti D58, D59; "insan" tek başına İnsan
# Kaynakları'na eşleniyordu)
_OPERATOR_PHRASES = ("insanla", "gercek biri", "gercek biriyle", "gercek bir kisi", "gercek bir kisiyle", "canli biri",
                     "canli biriyle", "temsilci", "temsilciyle", "musteri temsilcisi", "musteri temsilcisiyle")
_OPERATOR_WORDS = {"operator", "operatore", "santral", "santrale", "danisma", "resepsiyon", "yetkili"}
_DIGIT_WORDS = {"sifir": 0, "bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5, "alti": 6, "yedi": 7, "sekiz": 8,
                "dokuz": 9}
_TENS = {"on": 10, "yirmi": 20, "otuz": 30, "kirk": 40, "elli": 50, "altmis": 60, "yetmis": 70, "seksen": 80,
         "doksan": 90}


_NUM_SOFT = {"dort": "dord"}                     # ünsüz yumuşaması: dört → dördü, dörde
_NUM_SUFFIX = {"e", "a", "ye", "ya", "i", "u", "yi", "yu", "de", "da", "te", "ta", "den", "dan", "ten", "tan"}


def _number_word(w: str, in_run: bool) -> str | None:
    """Sayı sözcüğünün kökü; çekim ekli hâller de ("dörde", "dördü", "ikiye", "yüze"). "on" / "bir" ekli tek başına
    sayı sayılmaz ("ona bağlar mısınız", "biri"); sayının içindeyse sayılır ("iki yüz ona")."""
    if w in _DIGIT_WORDS or w in _TENS or w in ("yuz", "bin"):
        return w
    for base in (*_DIGIT_WORDS, *_TENS, "yuz", "bin"):
        for stem in (base, _NUM_SOFT.get(base)):
            if stem and w.startswith(stem) and w[len(stem):] in _NUM_SUFFIX:
                if not in_run and base in ("on", "bir"):
                    continue
                return base
    return None


def spoken_numbers(words: list[str]) -> list[str]:
    """Yazıyla söylenen dahili: "iki yüz on dört" → 214, "iki bir dört" → 214 (rakam rakam), çekim ekiyle de
    ("iki yüz on dörde" → 214; canlı deneme 03.11.2026'da ek "dörde" okunmayıp 210 çıkıyordu). STT bazen yazıyla
    yazar."""
    out: list[str] = []
    run: list[str] = []
    for w in [*words, ""]:
        if w and (base := _number_word(w, bool(run))) is not None:
            run.append(base)
            continue
        if len(run) >= 2 or (len(run) == 1 and run[0] in _TENS | {"yuz": 0}):
            if all(x in _DIGIT_WORDS for x in run):
                out.append("".join(str(_DIGIT_WORDS[x]) for x in run))
            else:
                total, cur = 0, 0
                for x in run:
                    if x == "yuz":
                        cur = (cur or 1) * 100
                    elif x == "bin":
                        total, cur = total + (cur or 1) * 1000, 0
                    else:
                        cur += _TENS.get(x) or _DIGIT_WORDS[x]
                out.append(str(total + cur))
        run = []
    return [n for n in out if 2 <= len(n) <= 6]
_FILLER = {"ist", "istiyor", "istiyoru", "istiyorum", "lutfen", "baglar", "baglarmis", "baglayin", "baglayabilir",
           "gorus", "gorusmek", "gorusebilir", "konus", "konusmak", "merhaba", "selam", "iyi", "gunler", "musunuz",
           "misiniz", "mi", "mu", "ile", "icin", "bir", "acaba", "rica", "ederim", "aktar", "aktarir", "aktarin",
           "orada", "var", "yok", "sey", "hani", "evet", "tamam", "beni", "bana", "kim", "kimle", "lazim"}
_ORDINALS = {"birinci": 0, "birincisi": 0, "ilk": 0, "ilki": 0, "ilkine": 0, "ikinci": 1, "ikincisi": 1,
             "ikinciye": 1, "ucuncu": 2, "ucuncusu": 2, "son": -1, "sonuncu": -1, "sonuncusu": -1, "sonuncuya": -1}
_VOWELS = "aıoueiöü"


def _last_vowel(word: str) -> str:
    return next((ch for ch in reversed(turkish_lower(word)) if ch in _VOWELS), "e")


def question_particle(word: str) -> str:
    """Soru eki (ünlü uyumu): Yılmaz → mı, Demir → mi, Koç → mu, Öztürk → mü."""
    return {"a": "mı", "ı": "mı", "o": "mu", "u": "mu", "e": "mi", "i": "mi", "ö": "mü", "ü": "mü"}[_last_vowel(word)]


def ablative(word: str) -> str:
    """Ayrılma hâli: Muhasebe → Muhasebeden, Satış → Satıştan, Bilgi İşlem → Bilgi İşlemden,
    İnsan Kaynakları → İnsan Kaynaklarından."""
    w = word.strip()
    if "-" in w or (w.isupper() and len(w) > 1):
        # kısaltma / tireli ad ("Ar-Ge", "BT"): ek kesmeyle ("Ar-Ge'den"); okunuşa göre ünlü uyumu
        last = turkish_lower(w)[-1:]
        hard = last in set("fstkçşhp")
        return f"{w}'" + ("t" if hard else "d") + ("a" if _last_vowel(w) in "aıou" else "e") + "n"
    last = turkish_lower(w)[-1:]
    # tamlama ("İnsan Kaynakları", "Müşteri Hizmetleri"): iyelik ekinden sonra -n- kaynaştırma → Kaynaklarından
    buffer = "n" if " " in w and last in "ıiuü" else ""
    hard = not buffer and last in set("fstkçşhp")
    return w + buffer + ("t" if hard else "d") + ("a" if _last_vowel(w) in "aıou" else "e") + "n"


def which_question(contacts: list[Any]) -> str | None:
    """"Muhasebeden Ayşe Yılmaz mı, Satıştan Ayşe Kaya mı?" — birimler farklıysa birimle, adlar farklıysa adla.
    Hiçbiri ayırt etmiyorsa None."""
    names = [c.full_name for c in contacts]
    depts = [c.department for c in contacts]
    # birim hatları ("Muhasebe mi, Teknik Destek mi?") birimle değil adla sorulur
    people = all(getattr(c, "kind", "person") == "person" for c in contacts)
    use_dept = people and all(depts) and len({search_key(d) for d in depts}) == len(depts)
    if not use_dept and len({search_key(n) for n in names}) < len(names):
        return None
    parts = [f"{ablative(d) + ' ' if use_dept else ''}{n} {question_particle(n)}" for n, d in zip(names, depts,
                                                                                                strict=True)]
    text = ", ".join(parts[:-1]) + (", yoksa " if len(parts) > 2 else ", ") + parts[-1] + "?"
    return text[0].upper() + text[1:]
