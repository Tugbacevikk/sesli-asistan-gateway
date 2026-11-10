"""Yüklenen rehberi mevcutla birleştirme ve karşılaştırma (önizleme), değişmeyen kayıtların zamanını koruma, CSV
dışa aktarma ve şablon."""
from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from typing import Any

from ..core.models import Contact
from .common import (
    EXPORT_HEADER,
    _excel_safe,
)
from .rows import (
    contact_record,
)


def merge(old: Sequence[Any], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """"Mevcut rehbere ekle": eski kayıtlar kalır, dosyadakiler eklenir; aynı kayıt (aynı kimlik: dosyadaki kimlik ya
    da aynı tür + ad + birim + dahili) dosyadakiyle güncellenir. Hiçbir kayıt silinmez."""
    out = {c.id: contact_record(c) for c in old}
    for rec in new:
        out[rec["id"]] = rec
    return list(out.values())


_COMPARED = ("kind", "first_name", "last_name", "department", "title", "extension", "transferable")


def diff(old: Sequence[Any], new: list[dict[str, Any]]) -> dict[str, Any]:
    """Önizleme için: eklenen / silinen / değişen kayıtlar (kimliğe göre)."""
    o = {c.id: c for c in old}
    nw = {c["id"]: c for c in new}

    def name(c: Any) -> str:
        if isinstance(c, dict):
            return Contact.from_api({"id": "", **c}).full_name
        return str(c.full_name)

    def changed(a: Any, b: dict[str, Any]) -> list[str]:
        out = [k for k in _COMPARED if str(getattr(a, k)) != str(b[k])]
        if sorted(a.aliases) != sorted(b["aliases"]) or sorted(a.department_aliases) != sorted(
                b["department_aliases"]):
            out.append("aliases")
        return out

    added = [nw[k] for k in nw.keys() - o.keys()]
    removed = [o[k] for k in o.keys() - nw.keys()]
    changes = [(nw[k], changed(o[k], nw[k])) for k in nw.keys() & o.keys()]
    changes = [(c, f) for c, f in changes if f]
    unchanged = len(nw.keys() & o.keys()) - len(changes)

    def brief(c: Any) -> dict[str, str]:
        if isinstance(c, dict):
            return {"name": name(c), "department": c["department"], "extension": c["extension"]}
        return {"name": name(c), "department": c.department, "extension": c.extension}

    return {"added": len(added), "removed": len(removed), "changed": len(changes), "unchanged": unchanged,
            "examples": {"added": [brief(c) for c in sorted(added, key=name)[:10]],
                         "removed": [brief(c) for c in sorted(removed, key=name)[:10]],
                         "changed": [{**brief(c), "fields": f} for c, f in sorted(changes,
                                                                                  key=lambda x: name(x[0]))[:10]]}}


def keep_unchanged_timestamps(old: Sequence[Any], new: list[dict[str, Any]]) -> None:
    """Değişmeyen kayıtların updated_at'i korunur (rehber API'sindeki artımlı güncelleme anlamı bozulmasın)."""
    o = {c.id: c for c in old}
    for c in new:
        prev = o.get(c["id"])
        if prev is not None and prev.updated_at and all(str(getattr(prev, k)) == str(c[k]) for k in _COMPARED) \
                and sorted(prev.aliases) == sorted(c["aliases"]) \
                and sorted(prev.department_aliases) == sorted(c["department_aliases"]):
            c["updated_at"] = prev.updated_at


def export_csv(contacts: Sequence[Any]) -> bytes:
    """Mevcut rehber → CSV (Excel'de açılır: UTF-8 BOM, ';' ayraç). Düzenlenip geri yüklenebilir (id korunur)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(EXPORT_HEADER)
    for c in sorted(contacts, key=lambda c: (c.department.casefold(), c.kind != "department",
                                             c.full_name.casefold())):
        row = [c.id, {"department": "birim", "role": "makam"}.get(c.kind, "kişi"), c.first_name, c.last_name,
               c.department,
               c.title, c.extension, ", ".join(c.aliases), ", ".join(c.department_aliases),
               "evet" if c.transferable else "hayır"]
        w.writerow([_excel_safe(v) for v in row])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def template_csv() -> bytes:
    """Boş şablon (örnek iki satırla)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(EXPORT_HEADER)
    w.writerow(["", "birim", "", "", "Muhasebe", "Birim hattı", "210", "", "Finans, Mali İşler", "evet"])
    w.writerow(["", "kişi", "Ayşe", "Yılmaz", "Muhasebe", "Muhasebe Uzmanı", "214", "Ayşe Hanım", "", "evet"])
    return ("﻿" + buf.getvalue()).encode("utf-8")
