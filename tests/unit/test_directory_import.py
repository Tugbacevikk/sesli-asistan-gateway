"""Rehber yükleme (tasarim.md 13.2): Excel / CSV okuma, doğrulama, önizleme, uygulama, geri alma, erişim."""
import io
import json
import shutil
import zipfile
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from test_ui import ROOT, Llm, Stt, Tts

from sesli_asistan.gateway.api.access import UiAccess
from sesli_asistan.gateway.api.app import create_app
from sesli_asistan.gateway.composition import GatewayRuntime
from sesli_asistan.gateway.config import GatewaySettings
from sesli_asistan.gateway.core.models import Contact, Match
from sesli_asistan.gateway.directory_import import (
    diff,
    export_csv,
    fold,
    parse,
    read_table,
    template_csv,
)

OCT = {"Content-Type": "application/octet-stream"}
JSON = {"Content-Type": "application/json"}


def xlsx(rows, inline=False, numbers=True):
    """Asgari .xlsx: paylaşılan metinler + sayı hücreleri (Excel'in yazdığı biçim)."""
    shared, sheet = [], []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, v in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if numbers and isinstance(v, (int, float)):
                cells.append(f'<c r="{ref}"><v>{float(v)}</v></c>')
            elif inline:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>')
            else:
                shared.append(str(v))
                cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
        sheet.append(f'<row r="{r}">{"".join(cells)}</row>')
    m = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{m}" xmlns:r="{rns}"><sheets>'
                                      f'<sheet name="Rehber" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{m}">' + "".join(f"<si><t>{s}</t></si>" for s in shared)
                   + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{m}"><sheetData>{"".join(sheet)}</sheetData>'
                                               "</worksheet>")
    return buf.getvalue()


ROWS = [["Adı", "SOYADI", "Birimi", "Görevi", "Dahili No"],
        ["Ayşe", "Yılmaz", "Muhasebe", "Uzman", 214],
        ["Mehmet", "Kaya", "Satış", "", 231]]


# --- okuma -----------------------------------------------------------------------------------------------------

def test_xlsx_paylasilan_ve_satir_ici_metin_sayilar_tamsayi():
    for inline in (False, True):
        rows = read_table(xlsx(ROWS, inline=inline), "rehber.xlsx")
        assert rows[1] == ["Ayşe", "Yılmaz", "Muhasebe", "Uzman", "214"]       # 214.0 → 214
        assert rows[2][3] == "" and rows[2][4] == "231"


def test_csv_utf8_bom_ve_turkce_windows_ayrac():
    text = "Ad Soyad;Birim;Dahili\r\nAyşe Yılmaz;Muhasebe;214\r\nİsmail Öztürk;Satış;231\r\n"
    for enc in ("utf-8-sig", "cp1254"):
        rows = read_table(text.encode(enc))
        assert rows[2] == ["İsmail Öztürk", "Satış", "231"]
    assert read_table(b"ad,soyad,dahili\nA,B,12\n")[1] == ["A", "B", "12"]
    assert read_table(b"ad\tsoyad\tdahili\nA\tB\t12\n")[1] == ["A", "B", "12"]


@pytest.mark.parametrize("data,msg", [
    (b"", "boş"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100, ".xls"),
    (b"PK\x03\x04bozuk", "bozuk"),
    (b"x" * (5 * 1024 * 1024 + 1), "5 MB"),
])
def test_okunamayan_dosya(data, msg):
    with pytest.raises(ValueError, match=msg):
        read_table(data)


def test_xlsx_varlik_saldirisi_ve_zip_bombasi_reddedilir():
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><workbook/>')
    with pytest.raises(ValueError, match="DOCTYPE"):
        read_table(evil.getvalue())
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("xl/worksheets/sheet1.xml", "0" * (61 * 1024 * 1024))
    assert len(bomb.getvalue()) < 1024 * 1024
    with pytest.raises(ValueError, match="çok büyük"):
        read_table(bomb.getvalue())


# --- doğrulama -------------------------------------------------------------------------------------------------

def test_basliklar_turkce_karakter_ve_buyuk_harften_bagimsiz():
    assert fold("  DAHİLİ No. ") == "dahili no" and fold("Görevi") == "gorevi"
    res = parse(read_table(xlsx(ROWS)))
    assert res.columns == {"first_name": "Adı", "last_name": "SOYADI", "department": "Birimi", "title": "Görevi",
                           "extension": "Dahili No"}
    assert [c["extension"] for c in res.contacts] == ["214", "231"] and not res.errors
    c = res.contacts[0]
    assert (c["kind"], c["first_name"], c["last_name"], c["transferable"]) == ("person", "Ayşe", "Yılmaz", True)


def test_ad_soyad_tek_sutun_ve_birim_hatti():
    rows = [["Ad Soyad", "Birim", "Dahili", "Takma adlar", "Birim takma adları"],
            ["Ayşe Nur Yılmaz", "Muhasebe", "214", "Ayşe Hanım; Nur", ""],
            ["", "Muhasebe", "210", "", "Finans, Mali İşler"]]
    res = parse(rows)
    p, d = res.contacts
    assert (p["first_name"], p["last_name"], p["aliases"]) == ("Ayşe Nur", "Yılmaz", ["Ayşe Hanım", "Nur"])
    assert d["kind"] == "department" and d["department_aliases"] == ["Finans", "Mali İşler"]


def test_hatali_satirlar_satir_numarasiyla():
    rows = [["Ad", "Soyad", "Birim", "Dahili", "Tür", "Aktarılabilir"],
            ["Ayşe", "Yılmaz", "Muhasebe", "214", "", ""],
            ["Ali", "", "Satış", "230", "", ""],              # soyad yok
            ["Veli", "Can", "Satış", "0532 123 45 67", "", ""],  # dış hat
            ["Can", "Ak", "Satış", "", "", ""],                # dahili boş
            ["", "", "", "", "", ""],                          # boş satır sayılmaz
            ["", "", "", "250", "birim", ""],                  # birim adı yok
            ["Ayşe", "Yılmaz", "Muhasebe", "214", "", ""],     # tekrar
            ["Su", "Ay", "Satış", "240", "müdür", ""],         # tür anlaşılmadı
            ["Ece", "Su", "Satış", "241", "", "belki"],        # aktarılabilir anlaşılmadı
            ["Deniz", "Er", "Satış", "242", "", "hayır"]]
    res = parse(rows)
    errs = {p.row: p.message for p in res.errors}
    assert set(errs) == {3, 4, 5, 7, 8, 9, 10}, errs
    assert "ad ve soyad" in errs[3] and "dış hat" in errs[4] and "boş" in errs[5]
    assert "birim adı" in errs[7] and "2. satırın tekrarı" in errs[8]
    assert res.rows == 9 and [c["extension"] for c in res.contacts] == ["214", "242"]
    assert res.contacts[1]["transferable"] is False


def test_uyarilar_ayni_dahili_ve_ayni_ad():
    rows = [["Ad", "Soyad", "Dahili"], ["Ayşe", "Yılmaz", "214"], ["Ayşe", "Yılmaz", "215"], ["Ali", "Can", "214"]]
    res = parse(rows)
    msgs = " | ".join(p.message for p in res.warnings)
    assert "214 dahilisi birden çok" in msgs and "hangisi olduğunu soracak" in msgs and not res.errors


def test_zorunlu_sutun_yoksa_acik_mesaj():
    with pytest.raises(ValueError, match="dahili numara sütunu bulunamadı.*Telefon Rehberi"):
        parse([["Telefon Rehberi", "x"], ["a", "b"]])


def test_kimlik_korunur_ve_disa_aktarma_geri_yuklenebilir():
    old = [Contact("c-101", "Ayşe", "Yılmaz", "Muhasebe", "214", updated_at="2026-01-01T00:00:00+00:00"),
           Contact("d-1", "", "", "Satış", "229", kind="department", department_aliases=("Pazarlama",))]
    res = parse([["Ad", "Soyad", "Birim", "Dahili"], ["AYŞE", "yılmaz", "Muhasebe", "214"],
                 ["Yeni", "Kişi", "Satış", "250"]], old)
    assert res.contacts[0]["id"] == "c-101"                      # aynı kişi: takma adlar kopmaz
    assert res.contacts[1]["id"].startswith("r-")
    again = parse([["Ad", "Soyad", "Birim", "Dahili"], ["Yeni", "Kişi", "Satış", "250"]])
    assert again.contacts[0]["id"] == res.contacts[1]["id"]      # yeni kayıtların kimliği de kararlı
    d = diff(old, res.contacts)
    assert (d["added"], d["removed"], d["changed"], d["unchanged"]) == (1, 1, 1, 0)   # ad büyük harf: değişti
    # dışa aktar → aynı dosyayı geri yükle: hiçbir şey değişmez
    exported = export_csv(old)
    assert exported.startswith(b"\xef\xbb\xbf")                  # Excel UTF-8'i tanısın
    back = parse(read_table(exported), old)
    assert [c["id"] for c in back.contacts] == ["c-101", "d-1"]
    assert diff(old, back.contacts)["changed"] == 0 and back.contacts[1]["department_aliases"] == ["Pazarlama"]
    assert not parse(read_table(template_csv())).errors


def test_csv_formul_enjeksiyonu_disa_aktarmada_etkisiz():
    old = [Contact("x1", "=HYPERLINK(\"http://kotu\")", "Ak", "Satış", "214")]
    text = export_csv(old).decode("utf-8-sig")
    assert ';"\'=HYPERLINK' in text
    assert parse(read_table(export_csv(old)), old).contacts[0]["first_name"].startswith("=HYPERLINK")


# --- uçtan uca (HTTP) ------------------------------------------------------------------------------------------

def client(tmp_path, key="", ui_users=""):
    path = tmp_path / "rehber.json"
    shutil.copy(ROOT / "data/rehber_ornek.json", path)
    s = GatewaySettings(_env_file=None, directory_path=str(path), local_settings_file=str(tmp_path / "ayar.json"),
                        ui_users=ui_users, api_key=key, call_history_db=str(tmp_path / "gecmis.db"))
    rt = GatewayRuntime(s, llm=Llm(), stt=Stt(), tts=Tts())

    @asynccontextmanager
    async def lifespan(app):
        await rt.start()
        yield
        await rt.stop()

    access = UiAccess(key, s.users(), {"testclient"})
    app = create_app(rt.manager, rt.health, api_key=key, lifespan=lifespan, web_session=rt.web_session,
                     settings=rt.settings, history=rt.history, ui_access=access)
    return TestClient(app), rt, path


def test_onizle_uygula_geri_al(tmp_path):
    tc, rt, path = client(tmp_path)
    with tc:
        before = len(rt.directory.index.source_contacts())
        st = tc.get("/v1/admin/settings").json()["directory_upload"]
        assert st["enabled"] and not st["revertible"]
        r = tc.post("/v1/admin/directory/preview", content=xlsx(ROWS), headers={**OCT, "X-Dosya-Adi": "rehber.xlsx"})
        assert r.status_code == 200, r.text
        p = r.json()
        assert p["contacts"] == 2 and p["errors"] == 0
        assert p["diff"]["removed"] + p["diff"]["changed"] + p["diff"]["unchanged"] == before
        assert len(p["sample"]) == 2
        assert rt.directory.index.search("mehmet kaya").match != Match.SINGLE_HIGH   # önizleme bir şey değiştirmez
        r = tc.post("/v1/admin/directory/apply", content=json.dumps({"token": p["token"]}), headers=JSON)
        assert r.status_code == 200, r.text
        assert "rehber yüklendi: 2 kayıt" in r.json()["message"]
        hit = rt.directory.index.search("mehmet kaya")
        assert hit.match == Match.SINGLE_HIGH and hit.candidates[0].contact.extension == "231"
        assert len(rt.directory.index.source_contacts()) == 2
        assert json.loads(path.read_text())["_kaynak"] == "arayüzden yüklendi"
        assert (tmp_path / "rehber.json.onceki").exists()
        # aynı belirteç ikinci kez kullanılamaz
        assert tc.post("/v1/admin/directory/apply", content=json.dumps({"token": p["token"]}),
                       headers=JSON).status_code == 400
        # değişiklik geçmişi
        ch = tc.get("/v1/admin/changes").json()["changes"]
        assert any("rehber yüklendi" in json.dumps(c, ensure_ascii=False) for c in ch)
        # geri al
        assert tc.get("/v1/admin/settings").json()["directory_upload"]["revertible"]
        r = tc.post("/v1/admin/directory/revert", content="{}", headers=JSON)
        assert r.status_code == 200, r.text
        assert len(rt.directory.index.source_contacts()) == before
        assert rt.directory.index.search("mehmet kaya").match != Match.SINGLE_HIGH
        # dışa aktarma
        r = tc.get("/v1/admin/directory/export")
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        assert len(read_table(r.content)) == before + 1


def test_hatali_satir_atlanmadan_uygulanmaz(tmp_path):
    tc, rt, _ = client(tmp_path)
    with tc:
        csv = "Ad;Soyad;Dahili\nAyşe;Yılmaz;214\nAli;;230\n".encode()
        p = tc.post("/v1/admin/directory/preview", content=csv, headers=OCT).json()
        assert p["errors"] == 1 and p["error_list"][0]["row"] == 3
        r = tc.post("/v1/admin/directory/apply", content=json.dumps({"token": p["token"]}), headers=JSON)
        assert r.status_code == 400 and "hatalı satırları atla" in r.json()["error"]["message"]
        r = tc.post("/v1/admin/directory/apply", content=json.dumps({"token": p["token"], "skip_errors": True}),
                    headers=JSON)
        assert r.status_code == 200 and "1 hatalı satır atlandı" in r.json()["message"]


def test_takma_ad_kopacaksa_uyarir(tmp_path):
    tc, rt, _ = client(tmp_path)
    with tc:
        assert tc.put("/v1/admin/settings", content=json.dumps({"aliases": {"c-105": ["Zeynep Hanım"]}}),
                      headers=JSON).status_code == 200
        p = tc.post("/v1/admin/directory/preview", content=xlsx(ROWS), headers=OCT).json()
        assert any("takma adlar etkisiz" in w["message"] for w in p["warnings"])


def test_erisim_icerik_turu_ve_boyut(tmp_path):
    tc, rt, _ = client(tmp_path, ui_users="Ali:izleyici:izleyici-anahtari-123", key="yonetici-anahtari-1234")
    with tc:
        tc.cookies.set("sesli_oturum", "izleyici-anahtari-123")
        r = tc.post("/v1/admin/directory/preview", content=xlsx(ROWS), headers=OCT)
        assert r.status_code == 403
        assert tc.get("/v1/admin/directory/export").status_code == 200     # izleyici görebilir
    (tmp_path / "b").mkdir()
    tc, rt, _ = client(tmp_path / "b")
    with tc:
        # form gönderimiyle (CSRF) yükleme olmaz
        assert tc.post("/v1/admin/directory/preview", content=b"a", headers={"Content-Type": "text/plain"}
                       ).status_code == 415
        big = b"a;b;dahili\n" + b"x" * (5 * 1024 * 1024 + 10)
        assert tc.post("/v1/admin/directory/preview", content=big, headers=OCT).status_code == 413
        # 512 KB'tan büyük ama 5 MB'tan küçük dosya kabul edilir
        rows = "Ad;Soyad;Dahili;Birim\n" + "".join(f"Ad{i};Soyad{i};{1000 + i % 9000};Birim {i % 50}\n"
                                                   for i in range(20000))
        assert 512 * 1024 < len(rows.encode()) < 5 * 1024 * 1024
        assert tc.post("/v1/admin/directory/preview", content=rows.encode(), headers=OCT).status_code == 200


def test_yazilamayan_rehber_dosyasinda_kapali(tmp_path):
    tc, rt, path = client(tmp_path)
    rt.settings.upload.path = tmp_path / "yok" / "rehber.json"
    with tc:
        st = tc.get("/v1/admin/settings").json()["directory_upload"]
        assert not st["enabled"] and "yazılamıyor" in st["reason"]
        r = tc.post("/v1/admin/directory/preview", content=xlsx(ROWS), headers=OCT)
        assert r.status_code == 400 and "yazılamıyor" in r.json()["error"]["message"]
