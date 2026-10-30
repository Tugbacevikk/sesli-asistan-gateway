#!/usr/bin/env bash
# Sesli asistan kurulum / güncelleme betiği (tasarim.md 14.3). Ubuntu 24.04, root olarak.
#
#   sudo deploy/install.sh                         # depodan kur / güncelle (internetli)
#   sudo deploy/install.sh --paket sesli-asistan-0.1.0.tar.gz   # internetsiz paketten (deploy/paket.sh üretir)
#   sudo deploy/install.sh --kontrol               # yalnızca ön kontroller
#   sudo deploy/install.sh --geri-al               # önceki sürüme dön
#   sudo deploy/install.sh --kaldir                # programı kaldır (ayarlar, çağrı geçmişi ve yedekler kalır)
#   sudo deploy/install.sh --kaldir --verilerle    # her şeyi kaldır (önce son bir yedek alınır; yedekler kalır)
#
# Seçenekler:
#   --servisler "gateway stt"   kurulacak servisler (tts: kadın sesi, isteğe bağlı)
#   --cpu                       GPU yok: STT işlemcide (yavaş; yalnız deneme), NVIDIA kontrolü atlanır
#   --onek /opt/sesli-asistan   kurulum kökü
#   --ollama-yok                Ollama'yı denetleme / model çekme (LLM başka sunucuda)
#   --baslatma                  servisleri kurup başlatma (yalnız dosyalar)
#   --evet                      kaldırmada onay sorma (otomasyon)
#   --yedeksiz                  --verilerle kaldırmada son yedeği alma
#   --imza-anahtari <dosya>     paket imzasını doğrulayacak açık anahtar (ilk kurulumda bir kez; $AYAR'a kopyalanır)
#   --imzasiz                   imza doğrulamadan kur (yalnız deneme; önerilmez)
#
# Ne yapar: ön kontrol → "sesli" kullanıcısı → releases/<sürüm>/ altında servis başına venv (kilitli sürümler,
# SHA256 doğrulamalı) → modeller (SHA256) → /etc/sesli-asistan ayarları (var olanın üzerine YAZMAZ) → systemd →
# current kısayolu → servisleri yeniden başlat → duman testi (/v1/health). Duman testi geçmezse önceki sürüme döner.
# Sistem paketleri, NVIDIA sürücüsü ve Ollama KURULMAZ; eksikse açıklamayla durur.
set -Eeuo pipefail

ONEK=/opt/sesli-asistan
# SESLI_* yalnız testler içindir (geçici klasörlerde kurulum / kaldırma denemesi)
AYAR=${SESLI_AYAR:-/etc/sesli-asistan}
VERI=${SESLI_VERI:-/var/lib/sesli-asistan}
SERVISLER="gateway stt"
PAKET=""
CPU=0
OLLAMA=1
BASLAT=1
KIP=kur
VERILERLE=0
EVET=0
YEDEKSIZ=0
IMZA_ANAHTARI=""
IMZASIZ=0
YEDEK=${SESLI_YEDEK:-/var/backups/sesli-asistan}
ONBELLEK=${SESLI_ONBELLEK:-/var/cache/sesli-asistan}
PY=python3.12
KAYNAK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

renk() { if [[ -t 1 ]]; then printf '\033[%sm%s\033[0m\n' "$1" "$2"; else printf '%s\n' "$2"; fi; }
bilgi() { renk "1;34" "==> $*"; }
uyari() { renk "1;33" "UYARI: $*"; }
dur() { renk "1;31" "HATA: $*"; exit 1; }
trap 'renk "1;31" "HATA: beklenmeyen hata (satır $LINENO): $BASH_COMMAND"' ERR

while [[ $# -gt 0 ]]; do
    case "$1" in
        --paket) PAKET=$(realpath "$2"); shift 2 ;;
        --servisler) SERVISLER="$2"; shift 2 ;;
        --cpu) CPU=1; shift ;;
        --onek) ONEK="$2"; shift 2 ;;
        --ollama-yok) OLLAMA=0; shift ;;
        --baslatma) BASLAT=0; shift ;;
        --kontrol) KIP=kontrol; shift ;;
        --geri-al) KIP=geri; shift ;;
        --kaldir) KIP=kaldir; shift ;;
        --verilerle) VERILERLE=1; shift ;;
        --evet) EVET=1; shift ;;
        --yedeksiz) YEDEKSIZ=1; shift ;;
        --imza-anahtari) IMZA_ANAHTARI=$(realpath "$2"); shift 2 ;;
        --imzasiz) IMZASIZ=1; shift ;;
        -h|--help) sed -n '2,29p' "$0"; exit 0 ;;
        *) dur "bilinmeyen seçenek: $1 (--help)" ;;
    esac
done
for s in $SERVISLER; do [[ $s =~ ^(gateway|stt|tts)$ ]] || dur "bilinmeyen servis: $s (gateway stt tts)"; done
has() { [[ " $SERVISLER " == *" $1 "* ]]; }

# --- ön kontroller -------------------------------------------------------------------------------------------
on_kontrol() {
    local ok=1
    [[ $EUID -eq 0 ]] || dur "root olarak çalıştırın (sudo)"
    if [[ -r /etc/os-release ]]; then
        . /etc/os-release
        [[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] || uyari "Ubuntu 24.04 bekleniyordu, bulunan: ${PRETTY_NAME:-?}"
    fi
    command -v systemctl >/dev/null || dur "systemd yok"
    if ! command -v $PY >/dev/null; then
        renk "1;31" "  ✗ $PY yok: sudo apt install python3.12 python3.12-venv"; ok=0
    elif ! $PY -c "import venv, ensurepip" 2>/dev/null; then
        renk "1;31" "  ✗ python3.12-venv yok: sudo apt install python3.12-venv"; ok=0
    else
        echo "  ✓ $($PY --version)"
    fi
    if has stt && [[ $CPU -eq 0 ]]; then
        if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
            echo "  ✓ GPU: $(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader | head -1)"
            local drv; drv=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1 | cut -d. -f1)
            [[ ${drv:-0} -ge 525 ]] || { renk "1;31" "  ✗ NVIDIA sürücüsü $drv: CUDA 12 için en az 525 gerekir"; ok=0; }
        else
            renk "1;31" "  ✗ nvidia-smi çalışmıyor: NVIDIA sürücüsünü kurun (ya da deneme için --cpu)"; ok=0
        fi
    fi
    local bos; bos=$(df -Pm "$(dirname "$ONEK")" | awk 'NR==2{print $4}')
    if [[ ${bos:-0} -lt 15000 ]]; then renk "1;31" "  ✗ $(dirname "$ONEK") altında boş alan ${bos} MB (en az 15000)"; ok=0
    else echo "  ✓ boş alan ${bos} MB"; fi
    # portlar: kendi servislerimiz çalışıyorsa (güncelleme) meşgul olmaları normaldir
    local p
    for p in 8000:gateway 50051:stt 9101:stt 5600:tts; do
        has "${p#*:}" || continue
        if ss -ltnH "sport = :${p%%:*}" 2>/dev/null | grep -q . && ! systemctl is-active -q "sesli-${p#*:}"; then
            renk "1;31" "  ✗ port ${p%%:*} başka bir programda (sesli-${p#*:} için gerekli)"; ok=0
        fi
    done
    if has gateway && [[ $OLLAMA -eq 1 ]]; then
        if command -v ollama >/dev/null; then echo "  ✓ ollama $(ollama --version 2>/dev/null | awk '{print $NF}')"
        else renk "1;31" "  ✗ Ollama yok: https://ollama.com/download/linux (LLM başka sunucudaysa --ollama-yok)"; ok=0; fi
    fi
    [[ -n "$PAKET" && ! -f "$PAKET" ]] && { renk "1;31" "  ✗ paket yok: $PAKET"; ok=0; }
    [[ $ok -eq 1 ]] || dur "ön kontroller geçmedi"
    bilgi "ön kontroller tamam"
}

# --- geri alma -----------------------------------------------------------------------------------------------
onceki_surum() {   # current dışındaki en yeni sürüm
    local cur; cur=$(readlink -f "$ONEK/current" 2>/dev/null || true)
    ls -1dt "$ONEK"/releases/*/ 2>/dev/null | sed 's:/$::' | grep -vx "${cur:-//}" | head -1
}

servisleri_yeniden_baslat() {
    local s
    has gateway && systemctl is-active -q sesli-gateway \
        && bilgi "Gateway yeniden başlatılıyor: açık çağrılar bitene kadar beklenir (en çok 2 dk, yeni çağrı alınmaz)"
    for s in stt tts gateway; do has $s && systemctl restart "sesli-$s"; done
}

saglik() {   # saglik <yol> → HTTP kodu; anahtar secrets.env'den (ekrana / ps çıktısına yazılmaz)
    local key; key=$(grep -m1 '^API_KEY=' "$AYAR/secrets.env" 2>/dev/null | cut -d= -f2- || true)
    curl -s -o /tmp/sesli-saglik.json -w '%{http_code}' -m 5 -K - "http://127.0.0.1:8000$1" <<< \
        "${key:+header = \"Authorization: Bearer $key\"}" || true
}

saglik_bekle() {   # Gateway hazır olana kadar (modeller ısınır): en çok SAGLIK_SN (180) sn
    has gateway || return 0
    for _ in $(seq 1 $((${SAGLIK_SN:-180} / 2))); do
        # /v1/admin/health: UI_ALLOWED_IPS (varsayılan 127.0.0.1); /v1/health: PBX_ALLOWED_IPS — biri yeter
        # yönetim ucu bozuk durumda da 200 döner: içerikteki "status" esas alınır
        if { [[ $(saglik /v1/admin/health) == 200 ]] || [[ $(saglik /v1/health) == 200 ]]; } \
                && grep -q '"status": *"ok"' /tmp/sesli-saglik.json; then
            bilgi "Gateway hazır: $(cat /tmp/sesli-saglik.json)"; rm -f /tmp/sesli-saglik.json; return 0
        fi
        sleep 2
    done
    renk "1;31" "Gateway ${SAGLIK_SN:-180} sn içinde hazır olmadı. Son durum:"
    saglik /v1/admin/health >/dev/null; cat /tmp/sesli-saglik.json 2>/dev/null || true; echo
    journalctl -u sesli-gateway -u sesli-stt -n 30 --no-pager || true
    return 1
}

geri_al() {
    local prev; prev=$(onceki_surum)
    [[ -n "$prev" ]] || dur "dönülecek önceki sürüm yok"
    bilgi "önceki sürüme dönülüyor: $(basename "$prev")"
    ln -sfn "$prev" "$ONEK/current.yeni" && mv -Tf "$ONEK/current.yeni" "$ONEK/current"
    servisleri_yeniden_baslat
    saglik_bekle
}

# --- kaldırma --------------------------------------------------------------------------------------------------
kaldir() {
    [[ $EUID -eq 0 ]] || dur "root olarak çalıştırın (sudo)"
    # yanlış --onek ile sistem klasörü silinmesin
    [[ "$ONEK" == /* && "$(basename "$ONEK")" == *sesli* ]] || dur "--onek beklenmedik: $ONEK"
    local birimler=(sesli-gateway sesli-stt sesli-tts sesli-yedek.timer sesli-yedek)
    echo "Kaldırılacak:"
    echo "  - servisler: ${birimler[*]} (açık çağrılar bitene kadar beklenir, en çok 2 dk)"
    echo "  - program, sanal ortamlar ve modeller: $ONEK"
    echo "  - seslendirme önbelleği: $ONBELLEK"
    if [[ $VERILERLE -eq 1 ]]; then
        echo "  - ayarlar ve şifreler: $AYAR"
        echo "  - çağrı geçmişi, yerel ayarlar, rehber: $VERI"
        echo "  - sistem kullanıcısı: sesli"
        [[ $YEDEKSIZ -eq 1 ]] && echo "  (son yedek ALINMAYACAK: --yedeksiz)" \
            || echo "  Önce son bir yedek alınır: $YEDEK"
    else
        echo "Kalacak: $AYAR (ayarlar), $VERI (çağrı geçmişi, rehber), $YEDEK (yedekler). Hepsi için: --verilerle"
    fi
    echo "Dokunulmayacak: Ollama ve dil modeli, NVIDIA sürücüsü, nginx, $YEDEK"
    if [[ $EVET -ne 1 ]]; then
        [[ -t 0 ]] || dur "onay gerekli: etkileşimsiz çalıştırmada --evet ekleyin"
        local cevap; read -r -p "Devam etmek için 'evet' yazın: " cevap
        [[ $cevap == evet ]] || dur "vazgeçildi"
    fi
    local py="$ONEK/current/venv/gateway/bin/python"
    if [[ $VERILERLE -eq 1 && $YEDEKSIZ -ne 1 ]] && compgen -G "$VERI/*" >/dev/null; then
        bilgi "son yedek (gizliler dahil): $YEDEK"
        [[ -x "$py" ]] || dur "yedek aracı yok ($py); yedek almadan kaldırmak için --yedeksiz"
        "$py" "$ONEK/current/tools/kurulum/yedek.py" al --hedef "$YEDEK" --tut 1000 --gizliler \
            --veri "$VERI" --ayar "$AYAR" \
            || dur "yedek alınamadı; kaldırma durduruldu (yedeksiz: --yedeksiz)"
    fi
    bilgi "servisler durduruluyor"
    local b
    for b in "${birimler[@]}"; do
        systemctl disable -q --now "$b" 2>/dev/null || true
        rm -f "/etc/systemd/system/$b.service" "/etc/systemd/system/$b"
    done
    systemctl daemon-reload
    systemctl reset-failed 'sesli-*' 2>/dev/null || true
    bilgi "program siliniyor: $ONEK"
    rm -rf "$ONEK" "$ONBELLEK"
    if [[ $VERILERLE -eq 1 ]]; then
        bilgi "ayarlar ve veriler siliniyor"
        rm -rf "$AYAR" "$VERI"
        id sesli >/dev/null 2>&1 && userdel sesli 2>/dev/null || true
    fi
    bilgi "kaldırma tamam"
    [[ -d "$YEDEK" ]] && echo "  Yedekler duruyor: $YEDEK (silmek için: sudo rm -rf $YEDEK)"
    [[ $VERILERLE -eq 0 ]] && echo "  Ayarlar ve veriler duruyor: $AYAR, $VERI (yeniden kurulumda kullanılır)"
    return 0
}

if [[ $KIP == kaldir ]]; then kaldir; exit 0; fi
if [[ $KIP == kontrol ]]; then on_kontrol; exit 0; fi
if [[ $KIP == geri ]]; then [[ $EUID -eq 0 ]] || dur "root gerekli"; geri_al; exit 0; fi
on_kontrol

# --- paket imzası ---------------------------------------------------------------------------------------------------
# SHA256SUMS paketin İÇİNDEDİR: bozulmayı yakalar, kasıtlı değişikliği yakalamaz (değiştiren listeyi de değiştirir).
# Bu yüzden paket, açılmadan önce sunucuda zaten bulunan açık anahtarla (paketten gelmeyen) doğrulanır: gpgv ayrı
# bir anahtar halkası kullanır, sistemin GPG ayarlarına dokunmaz. Anahtar ilk kurulumda --imza-anahtari ile bir kez
# konur (parmak izi yazdırılır: yayıncıdan ayrı bir kanaldan teyit edilir). Sonraki güncellemeler kurulu sürümün
# install.sh'ıyla (/opt/sesli-asistan/current/deploy/install.sh) yapılır: doğrulayan kod da paketten gelmez.
IMZA_HALKASI=$AYAR/imza-anahtari.gpg
imza_dogrula() {
    if [[ -n "$IMZA_ANAHTARI" ]]; then
        [[ -f "$IMZA_ANAHTARI" ]] || dur "açık anahtar yok: $IMZA_ANAHTARI"
        command -v gpg >/dev/null || dur "gpg yok: sudo apt install gnupg"
        bilgi "imza anahtarı (parmak izini yayıncıyla teyit edin):"
        gpg --batch --show-keys --with-fingerprint "$IMZA_ANAHTARI" | sed 's/^/    /'
        install -d -m 750 "$AYAR"
        local tmp; tmp=$(mktemp)
        gpg --batch --dearmor < "$IMZA_ANAHTARI" > "$tmp" 2>/dev/null || cp "$IMZA_ANAHTARI" "$tmp"
        install -m 644 "$tmp" "$IMZA_HALKASI"; rm -f "$tmp"   # root:root (yalnız root değiştirebilir)
    fi
    if [[ -f "$IMZA_HALKASI" ]]; then
        command -v gpgv >/dev/null || dur "gpgv yok: sudo apt install gpgv"
        [[ -f "$PAKET.sig" ]] || dur "imza dosyası yok: $PAKET.sig (paket imzalı olmalı)"
        gpgv --keyring "$IMZA_HALKASI" "$PAKET.sig" "$PAKET" 2>&1 | sed 's/^/    /' \
            || dur "paket İMZASI DOĞRULANAMADI: paket değiştirilmiş ya da başka anahtarla imzalanmış. Kurulmadı."
        bilgi "paket imzası doğrulandı"
    elif [[ $IMZASIZ -eq 1 ]]; then
        uyari "imza doğrulanmadan kuruluyor (--imzasiz)"
    else
        dur "imza anahtarı tanımlı değil ($IMZA_HALKASI): ilk kurulumda --imza-anahtari <açık-anahtar> verin (ya da yalnız deneme için --imzasiz)"
    fi
}

# --- kaynak: depo ya da internetsiz paket ----------------------------------------------------------------------
ACIK=""
if [[ -n "$PAKET" ]]; then
    imza_dogrula
    ACIK=$(mktemp -d)
    trap 'rm -rf "$ACIK"' EXIT
    bilgi "paket açılıyor: $PAKET"
    tar -xzf "$PAKET" -C "$ACIK"
    KAYNAK=$(find "$ACIK" -mindepth 1 -maxdepth 1 -type d | head -1)
    [[ -f "$KAYNAK/SHA256SUMS" ]] && (cd "$KAYNAK" && sha256sum --quiet -c SHA256SUMS) || dur "paket bütünlüğü doğrulanamadı"
    bilgi "paket bütünlüğü doğrulandı"
fi
[[ -f "$KAYNAK/pyproject.toml" || -d "$KAYNAK/tekerlek" ]] || dur "kaynak bulunamadı: $KAYNAK"
if [[ -f "$KAYNAK/SURUM" ]]; then
    SURUM=$(cat "$KAYNAK/SURUM")
else
    SURUM=$(grep -m1 '^version' "$KAYNAK/pyproject.toml" | cut -d'"' -f2)
fi
if [[ -z "$PAKET" ]] && git -C "$KAYNAK" rev-parse --short HEAD >/dev/null 2>&1; then
    SURUM="$SURUM-$(git -C "$KAYNAK" rev-parse --short HEAD)"
fi
HEDEF="$ONEK/releases/$SURUM"

# --- kullanıcı ve klasörler -----------------------------------------------------------------------------------
if ! id sesli >/dev/null 2>&1; then
    bilgi "sistem kullanıcısı: sesli"
    useradd --system --home-dir "$VERI" --shell /usr/sbin/nologin --user-group sesli
fi
install -d -m 755 "$ONEK" "$ONEK/releases" "$ONEK/models"
install -d -m 750 -o root -g sesli "$AYAR"
install -d -m 750 -o sesli -g sesli "$VERI"

# --- venv'ler (servis başına; kilitli sürümler + SHA256) --------------------------------------------------------
pip_kur() {   # pip_kur <venv> <kilit>
    local venv=$1 kilit=$2
    $PY -m venv "$venv"
    if [[ -n "$PAKET" ]]; then
        "$venv/bin/pip" install -q --no-index --find-links "$KAYNAK/tekerlek" --require-hashes -r "$kilit"
        "$venv/bin/pip" install -q --no-index --find-links "$KAYNAK/tekerlek" --no-deps sesli-asistan-gateway
    else
        "$venv/bin/pip" install -q --require-hashes -r "$kilit"
        "$venv/bin/pip" install -q --no-deps "$KAYNAK"
    fi
}
KILIT="$KAYNAK/deploy/kilit"
if [[ -d "$HEDEF/venv" ]]; then
    uyari "$SURUM zaten kurulu: venv'ler yeniden kuruluyor"
    rm -rf "$HEDEF/venv"
fi
install -d "$HEDEF/venv"
for s in $SERVISLER; do
    kilit="$KILIT/$s.txt"
    [[ $s == stt && $CPU -eq 0 ]] && kilit="$KILIT/stt-gpu.txt"
    bilgi "venv: $s ($(grep -c '==' "$kilit") paket, $(basename "$kilit"))"
    pip_kur "$HEDEF/venv/$s" "$kilit"
done
bilgi "venv: kurulum araçları"
pip_kur "$HEDEF/venv/araclar" "$KILIT/araclar.txt"
cp -r "$KAYNAK/deploy" "$KAYNAK/tools" "$HEDEF/"
cp "$KAYNAK/THIRD_PARTY_LICENSES.md" "$HEDEF/" 2>/dev/null || true
[[ -d "$KAYNAK/lisanslar" ]] && cp -r "$KAYNAK/lisanslar" "$HEDEF/"
echo "$SURUM" > "$HEDEF/SURUM"

# --- modeller (SHA256 doğrulamalı; sürümler arasında ortak) ---------------------------------------------------------
if has stt; then
    if [[ -n "$PAKET" ]]; then
        bilgi "modeller paketten kopyalanıyor"
        cp -rn "$KAYNAK/models/." "$ONEK/models/"
        "$HEDEF/venv/araclar/bin/python" "$HEDEF/tools/models/indir.py" --hedef "$ONEK/models" --dogrula
    else
        bilgi "modeller indiriliyor / doğrulanıyor"
        "$HEDEF/venv/araclar/bin/python" "$HEDEF/tools/models/indir.py" --hedef "$ONEK/models"
    fi
    chmod -R a+rX "$ONEK/models"
fi

# --- Ollama modeli ---------------------------------------------------------------------------------------------
if has gateway && [[ $OLLAMA -eq 1 ]]; then
    MODEL=$(grep -h '^LLM_MODEL=' "$AYAR/gateway.env" 2>/dev/null | cut -d= -f2 || true)
    MODEL=${MODEL:-qwen3.5:4b}
    if ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$MODEL"; then
        echo "  ✓ Ollama modeli var: $MODEL"
    elif [[ -n "$PAKET" ]]; then
        uyari "Ollama modeli $MODEL yok. İnternetli makinede 'ollama pull $MODEL' yapıp ~/.ollama/models klasörünü taşıyın"
    else
        bilgi "Ollama modeli çekiliyor: $MODEL"
        ollama pull "$MODEL"
    fi
fi

# --- ayarlar (var olanın üzerine yazılmaz) ---------------------------------------------------------------------------
ayar_kopyala() {   # ayar_kopyala <ad> <izin>
    local hedef="$AYAR/$1"
    if [[ -f "$hedef" ]]; then
        if ! diff -q <(grep -oE '^[A-Z_]+=' "$KAYNAK/deploy/ayar/$1" | sort) <(grep -oE '^[A-Z_]+=' "$hedef" | sort) >/dev/null; then
            uyari "$hedef korunuyor; yeni sürümde eklenen ayarlar için: diff $hedef $HEDEF/deploy/ayar/$1"
        fi
        return
    fi
    sed "s|@ONEK@|$ONEK|g" "$KAYNAK/deploy/ayar/$1" > "$hedef"
    chown root:sesli "$hedef"
    chmod "$2" "$hedef"
    echo "  + $hedef"
}
bilgi "ayarlar: $AYAR"
has gateway && ayar_kopyala gateway.env 640
has gateway && ayar_kopyala secrets.env 640
has stt && ayar_kopyala stt.env 640
has tts && ayar_kopyala tts.env 640
if has gateway && grep -q '^API_KEY=$' "$AYAR/secrets.env"; then
    sed -i "s|^API_KEY=$|API_KEY=$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 40)|" "$AYAR/secrets.env"
    echo "  + API_KEY üretildi (santral ekibine güvenli yoldan iletin: sudo grep API_KEY $AYAR/secrets.env)"
fi
# Rehber dosyası gateway.env'deki DIRECTORY_PATH'ten okunur; yoksa örnek rehber konur. /var/lib altındaysa
# Gateway yazabilir (arayüzden Excel/CSV yükleme), /etc altındaysa salt okunur kalır (eski kurulumlar).
if has gateway; then
    rehber=$(sed -n 's/^DIRECTORY_PATH=//p' "$AYAR/gateway.env" | tail -n1)
    rehber=${rehber:-$VERI/rehber.json}
    if [[ ! -f "$rehber" ]]; then
        install -d "$(dirname "$rehber")"
        cp "$KAYNAK/data/rehber_ornek.json" "$rehber" 2>/dev/null \
            || "$HEDEF/venv/gateway/bin/python" -c "import json;print(json.dumps({'contacts':[],'server_time':''}))" > "$rehber"
        uyari "örnek rehber kuruldu: $rehber — arayüzde Rehber → Rehber dosyası ile gerçek rehberi yükleyin"
    fi
    if [[ "$rehber" == "$VERI"/* ]]; then
        chown sesli:sesli "$rehber"; chmod 640 "$rehber"
    else
        chown root:sesli "$rehber"; chmod 640 "$rehber"
        uyari "rehber $rehber: arayüzden yükleme kapalı (Gateway yazamaz). Açmak için dosyayı $VERI/ altına taşıyıp DIRECTORY_PATH'i güncelleyin"
    fi
fi
# Ayarların doğruluğu (servis açılmadan): pydantic doğrulaması
# (bash ile "source" edilmez: systemd biçiminde tırnaksız boşluklu değerler olabilir)
if has gateway; then
    (cd "$VERI" && env -i PATH="$PATH" "$HEDEF/venv/gateway/bin/python" -c "
from sesli_asistan.gateway.config import GatewaySettings as S
S(_env_file=('$AYAR/gateway.env', '$AYAR/secrets.env'))") \
        || dur "gateway.env geçersiz (yukarıdaki hata); düzeltip yeniden çalıştırın"
fi

# --- systemd ve sürüm geçişi ------------------------------------------------------------------------------------------
bilgi "systemd servisleri"
for s in $SERVISLER; do
    sed "s|@ONEK@|$ONEK|g" "$KAYNAK/deploy/systemd/sesli-$s.service" > "/etc/systemd/system/sesli-$s.service"
done
if has gateway; then   # günlük yedek (tasarim.md 14.7)
    sed "s|@ONEK@|$ONEK|g" "$KAYNAK/deploy/systemd/sesli-yedek.service" > /etc/systemd/system/sesli-yedek.service
    cp "$KAYNAK/deploy/systemd/sesli-yedek.timer" /etc/systemd/system/sesli-yedek.timer
fi
systemctl daemon-reload
ONCEKI=$(readlink -f "$ONEK/current" 2>/dev/null || true)
# güncellemeden önce yedek: yeni sürüm veritabanını değiştirirse geri dönülebilsin
if has gateway && [[ -n "$ONCEKI" ]] && compgen -G "$VERI/*.db" >/dev/null; then
    bilgi "güncelleme öncesi yedek"
    "$HEDEF/venv/gateway/bin/python" "$HEDEF/tools/kurulum/yedek.py" al --hedef /var/backups/sesli-asistan --tut 14 \
        || dur "yedek alınamadı; güncelleme durduruldu"
fi
ln -sfn "$HEDEF" "$ONEK/current.yeni" && mv -Tf "$ONEK/current.yeni" "$ONEK/current"
bilgi "etkin sürüm: $SURUM"

if [[ $BASLAT -eq 0 ]]; then
    bilgi "servisler başlatılmadı (--baslatma). Başlatmak için: systemctl enable --now $(for s in $SERVISLER; do printf 'sesli-%s ' "$s"; done)"
    exit 0
fi
for s in $SERVISLER; do systemctl enable -q "sesli-$s"; done
has gateway && systemctl enable -q --now sesli-yedek.timer
servisleri_yeniden_baslat
if ! saglik_bekle; then
    if [[ -n "$ONCEKI" && "$ONCEKI" != "$HEDEF" ]]; then
        uyari "duman testi geçmedi: önceki sürüme ($(basename "$ONCEKI")) dönülüyor"
        ln -sfn "$ONCEKI" "$ONEK/current.yeni" && mv -Tf "$ONEK/current.yeni" "$ONEK/current"
        servisleri_yeniden_baslat
        saglik_bekle || true
    fi
    dur "kurulum tamamlanamadı (bkz. yukarıdaki log)"
fi

# eski sürümler: son 3 tutulur
ls -1dt "$ONEK"/releases/*/ | tail -n +4 | xargs -r rm -rf
bilgi "kurulum tamam: $SURUM"
echo "  Arayüz (sunucuda): http://127.0.0.1:8000/ui   · uzaktan HTTPS: $HEDEF/deploy/nginx/sesli-asistan.conf"
echo "  Loglar: journalctl -u sesli-gateway -u sesli-stt -f    · geri alma: sudo $HEDEF/deploy/install.sh --geri-al"
echo "  Sorun bildirimi: sudo python3 $ONEK/current/tools/kurulum/destek.py   · kaldırma: sudo $HEDEF/deploy/install.sh --kaldir"
