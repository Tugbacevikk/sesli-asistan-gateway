"""Gateway'i bakım için boşaltır: yeni çağrı alınmaz (sağlık 503 "draining": santral operatöre yönlendirir), açık
çağrıların bitmesi en çok --bekle sn beklenir. systemd ExecStop'ta çalışır: yeniden başlatma / güncelleme açık
çağrıları kesmez (tasarim.md 14.3). Yalnız standart kütüphane; yalnız sunucunun kendisinden çağrılabilir.

    python bosalt.py --bekle 120          # boşalt ve bekle
    python bosalt.py --iptal              # vazgeç: yeni çağrılar yeniden alınsın
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("GATEWAY_API_PORT", "8000")))
    ap.add_argument("--bekle", type=float, default=120.0)
    ap.add_argument("--iptal", action="store_true")
    a = ap.parse_args(argv)
    body = {"iptal": True} if a.iptal else {"bekle_sn": a.bekle}
    req = urllib.request.Request(f"http://127.0.0.1:{a.port}/v1/yerel/bosalt", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=a.bekle + 15) as r:   # nosec B310 (sabit yerel adres)
            out = json.load(r)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        print(f"Gateway'e ulaşılamadı ({e}); zaten kapalı sayılıyor")
        return 0
    if a.iptal:
        print("boşaltma iptal: yeni çağrılar alınıyor")
    elif out["active"]:
        print(f"süre doldu, {out['active']} çağrı hâlâ açık: kapanışta santrale bırakılacak")
    else:
        print("açık çağrı kalmadı")
    return 0


if __name__ == "__main__":
    sys.exit(main())
