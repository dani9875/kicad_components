#!/usr/bin/env python3
"""Sync Inventory.xlsx with pepy_sym_lib.

    ./inventory_sync.py ~/Documents/Inventory.xlsx
    ./inventory_sync.py ~/Documents/Inventory.xlsx --add-missing

What it does (a backup Inventory.xlsx.bak is written first):
  Library sheet         rebuilt from the library: every symbol with MPN, LCSC, footprint.
                        "In inventory?" and "PCS" are formulas over the Inventory sheet.
  Inventory sheet       column M "LCSC" filled from the URL (or kept if you typed it).
                        column K "In the library?" becomes a formula over the Library sheet.
                        column L "Footprint" is filled when empty or UNASSIGNED.
  Footprint_validation  list of footprints for the column L dropdown, from the library.
  --add-missing         appends every library part that is not in the inventory yet,
                        filled in, with PCS empty (yellow) for you to count.
Matching uses the LCSC number first, then the MPN.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
except ImportError:
    sys.exit("openpyxl is missing. Run: pip install openpyxl")

from libtools.library import FP_DIR, LIB_NAME, SYM_LIB, load_parts

INV, LIB, FPV = "Inventory", "Library", "Footprint_validation"
# Inventory columns (1-based)
C_NUM, C_NAME, C_MFR, C_MPN, C_COMMENT, C_PCS, C_URL, C_DS, C_TYPE, C_PKG, C_INLIB, C_FP, C_LCSC = range(1, 14)
FONT = "Arial"
FILL_INPUT = PatternFill("solid", fgColor="FFFF99")

TYPE_RULES = [  # [CATEGORY] pattern -> Type text used in the Inventory sheet (first match wins)
    (r"FERRITE", "Ferrite bead"), (r"POWER_IND|P_IND", "Power inductor"), (r"(^|_)IND", "Inductor"),
    (r"(^|_)RES", "Resistor"), (r"(^|_)CAP", "Capacitor"), (r"LED_DRIVER", "LED driver"),
    (r"7SEG", "7-segment display"), (r"(^|_)LED", "LED"), (r"TVS|ESD", "ESD protection"),
    (r"SCHOTTKY|DIODE|RECT|ZENER", "Diode"), (r"LDO", "LDO"), (r"BUCK", "Buck DC/DC"),
    (r"BOOST", "Boost DC/DC"), (r"EFUSE", "EFUSE"), (r"LOAD_SWITCH", "Load switch"),
    (r"MCU|SOC", "MCU"), (r"FLIP|BUFFER|COUNTER|GATE|LOGIC|LEVEL_SHIFT|ADDER|COMPARATOR|SHIFT_REG|DEMUX|SCHMITT", "Logic"),
    (r"OPAMP", "Op-amp"), (r"PMIC|STEPD", "Power IC"),
    (r"DDR|EMMC|FLASH|EEPROM|SRAM", "Memory"), (r"XTAL|CRYSTAL|OSC", "Crystal"),
    (r"PMOS", "PMOS"), (r"NMOS", "NMOS"), (r"MOSFET", "MOSFET"),
    (r"IDC", "IDC header"), (r"HEADER|DIL", "Pin header"), (r"JST", "JST Connector"), (r"HCTL|MOLEX", "Wire-to-board connector"),
    (r"SD_PUSH|U_SIM|SIM", "Card socket"), (r"R45|RJ45", "RJ45 Connector"),
    (r"USB", "USB Connector"), (r"RJ45", "RJ45 Connector"), (r"FPC|FFC", "FPC Connector"),
    (r"SCREW|TERMINAL", "Screw terminal"), (r"CONN|SOCKET", "Connector"), (r"ANTENNA", "Antenna"),
    (r"TACTILE", "Tactile switch"), (r"SLIDE", "Slide switch"), (r"SWITCH|BUTTON", "Switch"),
    (r"COMMON_MODE", "Common mode filter"), (r"FUSE", "Fuse"), (r"SUPERVISOR", "Voltage supervisor"),
]


def lcsc_of(url) -> str:
    m = re.search(r"[_/](C\d+)\.html", str(url or ""))
    return m.group(1) if m else ""


def norm(s) -> str:
    return re.sub(r"\s+", "", str(s or "")).upper()


def type_of(category: str) -> str:
    c = category.strip("[]").upper()
    for rx, t in TYPE_RULES:
        if re.search(rx, c):
            return t
    return c.replace("_", " ").title()


def package_of(p) -> str:
    pkg = p.fields.get("Package", "").strip()
    if pkg:
        return pkg
    m = re.search(r"_(0201|0402|0603|0805|1206|1210|2010|2512)_", p.footprint or "")
    return m.group(1) if m else "N/A"


def comment_of(p) -> str:
    f = p.fields
    if f.get("Nvalue"):
        bits = [f["Nvalue"], f.get("Voltage rating", ""), f.get("Dielectric", ""), f.get("Tolerance", "")]
        return ", ".join(b for b in bits if b)
    return f.get("Description", "")


def last_row(ws) -> int:
    for r in range(ws.max_row, 1, -1):
        if any(ws.cell(r, c).value not in (None, "") for c in range(1, 14)):
            return r
    return 1


def fill_row(ws, r: int, p, pcs=None, comment: str = "") -> None:
    f = p.fields
    mpn, mfr = f.get("Value", ""), f.get("Manufacturer", "")
    lc = f.get("LCSC Part", "")
    vals = {C_NAME: f"{mfr} {mpn}".strip(), C_MFR: mfr, C_MPN: mpn,
            C_COMMENT: " — ".join(x for x in (comment, comment_of(p)) if x),
            C_URL: f"https://www.lcsc.com/product-detail/{lc}.html" if lc else "",
            C_DS: f.get("Datasheet", ""), C_TYPE: type_of(p.category), C_PKG: package_of(p),
            C_FP: p.footprint or "UNASSIGNED", C_LCSC: lc}
    for c, v in vals.items():
        cell = ws.cell(r, c, v or None)
        cell.font = Font(name=FONT)
        if c in (C_URL, C_DS):
            cell.hyperlink = v if str(v or "").startswith("http") else None
    cell = ws.cell(r, C_PCS, pcs)
    cell.font = Font(name=FONT)
    if pcs in (None, ""):
        cell.fill = FILL_INPUT


def build_library_sheet(wb, parts) -> None:
    if LIB in wb.sheetnames:
        del wb[LIB]
    ws = wb.create_sheet(LIB, index=wb.sheetnames.index(INV) + 1)
    head = ["Symbol", "Category", "MPN (Value)", "LCSC", "Manufacturer", "Description",
            "Footprint", "In inventory?", "PCS", "Datasheet"]
    widths = [70, 28, 26, 12, 26, 60, 60, 14, 8, 60]
    for i, (h, w) in enumerate(zip(head, widths), start=1):
        c = ws.cell(1, i, h)
        c.font = Font(name=FONT, bold=True)
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    q = f"'{INV}'!"
    for r, p in enumerate(sorted(parts, key=lambda x: (x.category.lower(), x.name.lower())), start=2):
        f = p.fields
        row = [p.name, p.category.strip("[]"), f.get("Value", ""), f.get("LCSC Part", ""),
               f.get("Manufacturer", ""), f.get("Description", ""), p.footprint,
               (f'=IF(OR(AND(D{r}<>"",COUNTIF({q}$M:$M,D{r})>0),'
                f'AND(C{r}<>"",COUNTIF({q}$D:$D,C{r})>0)),"Yes","No")'),
               (f'=IF(AND(D{r}<>"",COUNTIF({q}$M:$M,D{r})>0),SUMIF({q}$M:$M,D{r},{q}$F:$F),'
                f'IF(C{r}<>"",SUMIF({q}$D:$D,C{r},{q}$F:$F),0))'),
               f.get("Datasheet", "")]
        for c, v in enumerate(row, start=1):
            ws.cell(r, c, v if v != "" else None).font = Font(name=FONT)
    n = len(parts) + 1
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:J{n}"
    from openpyxl.formatting.rule import FormulaRule
    ws.conditional_formatting.add(f"H2:H{n}", FormulaRule(
        formula=['$H2="No"'], fill=PatternFill("solid", fgColor="F4CCCC")))


def update_footprint_list(wb) -> None:
    ws = wb[FPV]
    names = sorted({f.stem for f in FP_DIR.glob("*.kicad_mod")}, key=str.lower)
    for r in range(2, max(ws.max_row, 2) + 1):
        ws.cell(r, 1).value = None
    for r, v in enumerate(["N/A", "UNASSIGNED"] + names, start=2):
        ws.cell(r, 1, v).font = Font(name=FONT)
    return len(names) + 3


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--add-missing", action="store_true", help="Append library parts not in the inventory")
    a = ap.parse_args()

    parts = load_parts(SYM_LIB)
    by_lcsc = {p.fields.get("LCSC Part", "").strip(): p for p in parts if p.fields.get("LCSC Part")}
    by_mpn = {norm(p.fields.get("Value")): p for p in parts if p.fields.get("Value")}

    shutil.copy(a.xlsx, a.xlsx.with_suffix(".xlsx.bak"))
    wb = openpyxl.load_workbook(a.xlsx)
    ws = wb[INV]
    ws.cell(1, C_LCSC, "LCSC").font = Font(name=FONT, bold=True)
    ws.column_dimensions["M"].width = 12
    end = last_row(ws)

    seen, filled_fp = set(), 0
    for r in range(2, end + 1):
        mpn = ws.cell(r, C_MPN).value
        if not mpn and not ws.cell(r, C_URL).value:
            continue
        lc = (ws.cell(r, C_LCSC).value or lcsc_of(ws.cell(r, C_URL).value) or "").strip()
        if lc:
            ws.cell(r, C_LCSC, lc).font = Font(name=FONT)
        p = by_lcsc.get(lc) or by_mpn.get(norm(mpn))
        if p:
            seen.add(p.name)
            if ws.cell(r, C_FP).value in (None, "", "UNASSIGNED") and p.footprint:
                ws.cell(r, C_FP, p.footprint)
                filled_fp += 1
        k = ws.cell(r, C_INLIB)
        k.value = (f'=IF(OR(AND($M{r}<>"",COUNTIF({LIB}!$D:$D,$M{r})>0),'
                   f'AND($D{r}<>"",COUNTIF({LIB}!$C:$C,$D{r})>0)),"Yes","No")')
        k.font = Font(name=FONT)

    added = 0
    if a.add_missing:
        r = end + 1
        for p in sorted(parts, key=lambda x: (x.category.lower(), x.name.lower())):
            if p.name in seen or p.name.startswith(("[TEST_POINT]", "[MOUNTING_HOLE]")):
                continue
            fill_row(ws, r, p)
            ws.cell(r, C_INLIB, f'=IF(COUNTIF({LIB}!$D:$D,$M{r})>0,"Yes","No")').font = Font(name=FONT)
            r += 1
            added += 1
        end = r - 1

    n_fp = update_footprint_list(wb)
    build_library_sheet(wb, parts)

    # dropdowns and filter cover all rows
    ws.data_validations.dataValidation = []
    dv_k = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
    dv_l = DataValidation(type="list", formula1=f"{FPV}!$A$2:$A${max(n_fp, 301)}", allow_blank=True)
    dv_k.add(f"K2:K{end}")
    dv_l.add(f"L2:L{end}")
    ws.add_data_validation(dv_k)
    ws.add_data_validation(dv_l)
    ws.auto_filter.ref = f"A1:M{end}"
    wb.save(a.xlsx)
    print(f"{a.xlsx}: {len(parts)} library parts, {len(seen)} matched in inventory, "
          f"{filled_fp} footprints filled, {added} parts added. Backup: {a.xlsx.with_suffix('.xlsx.bak')}")
    print(f"Library source: {SYM_LIB} ({LIB_NAME})")
    return 0


if __name__ == "__main__":
    sys.exit(main())