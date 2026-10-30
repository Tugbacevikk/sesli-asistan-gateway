#!/usr/bin/env bash
# Deneme santrali (Asterisk) ayarlarını kurar: Ubuntu / WSL. Var olan ayarlar /etc/asterisk/yedek-<zaman>/ altına alınır.
#   sudo apt install -y asterisk
#   sudo deploy/asterisk/kur.sh
# Çıktı: dahili şifreleri ve ARI şifresi (deploy/asterisk/sifreler.txt; repoya girmez).
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
[[ $EUID -eq 0 ]] || { echo "root olarak çalıştırın (sudo)"; exit 1; }
command -v asterisk >/dev/null || { echo "Asterisk yok: sudo apt install -y asterisk"; exit 1; }

sifre() { head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 16; }
if [[ -f sifreler.txt ]]; then
    # yeniden kurulumda aynı şifreler (telefonları yeniden ayarlamak gerekmesin)
    # shellcheck disable=SC1091
    source sifreler.txt
fi
ARI_SIFRE=${ARI_SIFRE:-$(sifre)}
SIFRE_1001=${SIFRE_1001:-$(sifre)}
SIFRE_214=${SIFRE_214:-$(sifre)}
SIFRE_231=${SIFRE_231:-$(sifre)}
SIFRE_900=${SIFRE_900:-$(sifre)}

yedek=/etc/asterisk/yedek-$(date +%Y%m%d-%H%M%S)
mkdir -p "$yedek"
for f in http.conf ari.conf rtp.conf pjsip.conf extensions.conf modules.conf; do
    [[ -f /etc/asterisk/$f ]] && cp "/etc/asterisk/$f" "$yedek/"
    sed -e "s|@ARI_SIFRE@|$ARI_SIFRE|" -e "s|@SIFRE_1001@|$SIFRE_1001|" -e "s|@SIFRE_214@|$SIFRE_214|" \
        -e "s|@SIFRE_231@|$SIFRE_231|" -e "s|@SIFRE_900@|$SIFRE_900|" "$f" > "/etc/asterisk/$f"
    chown asterisk:asterisk "/etc/asterisk/$f" 2>/dev/null || true
    chmod 640 "/etc/asterisk/$f"
done
umask 077
cat > sifreler.txt <<EOT
ARI_SIFRE=$ARI_SIFRE
SIFRE_1001=$SIFRE_1001
SIFRE_214=$SIFRE_214
SIFRE_231=$SIFRE_231
SIFRE_900=$SIFRE_900
EOT
[[ -n "${SUDO_USER:-}" ]] && chown "$SUDO_USER" sifreler.txt

if command -v systemctl >/dev/null && systemctl is-system-running >/dev/null 2>&1; then
    systemctl restart asterisk
else
    service asterisk restart
fi
sleep 2
echo "== Asterisk: $(asterisk -rx 'core show version' | head -1)"
asterisk -rx "pjsip show endpoints" | grep -E "Endpoint:" || true
asterisk -rx "ari show apps" || true
IP=$(hostname -I | awk '{print $1}')
cat <<EOT

Telefon hesapları (SIP sunucusu: $IP, port 5060, UDP; kodek: yalnız G.711 A-law / PCMA):
  1001 (arayan, MicroSIP)   şifre: $SIFRE_1001
  214  (Ayşe Yılmaz)         şifre: $SIFRE_214
  231  (Ayşe Kaya)           şifre: $SIFRE_231
  900  (operatör)            şifre: $SIFRE_900
ARI: kullanıcı sesli, şifre: $ARI_SIFRE   (köprü: --ari-sifre)
Şifreler: $(pwd)/sifreler.txt
EOT
