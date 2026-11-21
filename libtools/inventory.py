"""Read stock counts from Inventory.xlsx (sheet "Inventory").

Columns used: A "#", D "Mfr. nr.", F "PCS", G "URL(s)", M "LCSC" (optional).
A part matches by LCSC number first, then by MPN. Several rows for one part are summed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SHEET = "Inventory"


@dataclass
class Stock:
    pcs: float = 0
    rows: list[int] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)   # values of the "#" column
    counted: bool = False                              # at least one row has a PCS value


def norm(s) -> str:
    return re.sub(r"\s+", "", str(s or "")).upper()


def lcsc_of(url) -> str:
    m = re.search(r"[_/](C\d+)\.html", str(url or ""))
    return m.group(1) if m else ""


class Inventory:
    def __init__(self):
        self.path: Path | None = None
        self.by_lcsc: dict[str, Stock] = {}
        self.by_mpn: dict[str, Stock] = {}
        self.error = ""

    def load(self, path: Path) -> None:
        self.path, self.error = path, ""
        self.by_lcsc, self.by_mpn = {}, {}
        try:
            import openpyxl
        except ImportError:
            self.error = "openpyxl is missing (pip install openpyxl)"
            return
        try:
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            ws = wb[SHEET]
        except Exception as e:  # noqa: BLE001
            self.error = f"Cannot read {path.name}: {e}"
            return
        for r, row in enumerate(ws.iter_rows(min_row=2, max_col=13, values_only=True), start=2):
            row = list(row) + [None] * (13 - len(row))
            num, mpn, pcs, url, lc = row[0], row[3], row[5], row[6], row[12]
            lc = str(lc or "").strip() or lcsc_of(url)
            if not mpn and not lc:
                continue
            for key, table in ((lc, self.by_lcsc), (norm(mpn), self.by_mpn)):
                if not key:
                    continue
                st = table.setdefault(key, Stock())
                st.rows.append(r)
                if num not in (None, ""):
                    st.numbers.append(str(int(num)) if isinstance(num, float) else str(num))
                if isinstance(pcs, (int, float)):
                    st.pcs += pcs
                    st.counted = True
        wb.close()

    @property
    def loaded(self) -> bool:
        return self.path is not None and not self.error

    def find(self, lcsc: str, mpn: str) -> Stock | None:
        return self.by_lcsc.get((lcsc or "").strip()) or self.by_mpn.get(norm(mpn))