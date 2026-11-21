"""Library checks. Each finding names the symbol or footprint it belongs to."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .library import FP_DIR, LIB_NAME, ORIG_DIR, PASSIVE_REFS, Part, load_footprint, load_parts
from .sexp import kid, kids, walk

ERR, WARN = "ERROR", "WARN"
VIRTUAL = ("[TEST_POINT]", "[MOUNTING_HOLE]")


@dataclass
class Finding:
    level: str
    rule: str
    kind: str      # "symbol" or "footprint"
    item: str      # symbol or footprint name
    detail: str = ""


def lint_symbols(parts: list[Part]) -> list[Finding]:
    out: list[Finding] = []
    fps = {f[:-10] for f in os.listdir(FP_DIR) if f.endswith(".kicad_mod")}
    seen: dict[str, int] = {}

    def add(level, rule, p, detail=""):
        out.append(Finding(level, rule, "symbol", p.name, detail))

    for p in parts:
        seen[p.name] = seen.get(p.name, 0) + 1
        f = p.fields
        fp = f.get("Footprint", "")
        if not fp:
            add(ERR, "no footprint", p)
        elif fp.startswith(LIB_NAME + ":") and p.footprint not in fps:
            add(ERR, "footprint not in .pretty", p, fp)
        for u in kids(p.node, "symbol"):
            if not u[1].startswith(p.name + "_"):
                add(ERR, "unit name does not match symbol", p, u[1])
        lc = f.get("LCSC Part", "").strip()
        m = re.search(r"[_/](C\d+)\.(?:html|pdf)", f.get("Datasheet", "").split("?")[0])
        if lc and m and m.group(1) != lc:
            add(ERR, "datasheet belongs to another LCSC part", p, f"LCSC {lc}, datasheet {m.group(1)}")

        if not p.name.startswith(VIRTUAL):
            for k in ("Description", "Manufacturer", "LCSC Part"):
                if not f.get(k, "").strip():
                    add(WARN, f"no {k}", p)
            if not f.get("ki_keywords", "").strip():
                add(WARN, "no keywords", p)
            if p.ref in ("R", "C", "L", "FB"):
                if not f.get("Nvalue", "").strip() or f.get("Nvalue") == "?":
                    add(ERR, "passive without Nvalue", p)
                if not f.get("Tolerance", "").strip():
                    add(WARN, "passive without Tolerance", p)
                elif not f["Tolerance"].strip().startswith("±"):
                    add(WARN, "Tolerance not written as ±x%", p, f["Tolerance"])
                if p.ref == "C" and not f.get("Voltage rating", "").strip():
                    add(WARN, "capacitor without Voltage rating", p)
        tol, vr = f.get("Tolerance", "").strip(), f.get("Voltage rating", "").strip()
        if tol and "%" not in tol and "ppm" not in tol:
            add(WARN, "Tolerance is not a percentage (swapped fields?)", p, tol)
        if vr and not re.search(r"\dk?V$", vr):
            add(WARN, "Voltage rating is not a voltage (swapped fields?)", p, vr)
        mfr = f.get("Manufacturer", "")
        if re.search(r"[　-鿿]", mfr):
            add(WARN, "Chinese brand suffix in Manufacturer", p, mfr)
        for k, v in f.items():
            if v != v.strip():
                add(WARN, "leading/trailing whitespace in field", p, k)
        if "product-detail" in f.get("Datasheet", ""):
            add(WARN, "datasheet is an LCSC shop page, not a PDF", p)

        pins = p.pins
        nums = [kid(x, "number")[1] for x in pins if kid(x, "number")]
        dup = sorted({n for n in nums if nums.count(n) > 1})
        if dup:
            add(WARN, "duplicate pin numbers", p, str(dup[:6]))
        off = sum(1 for x in pins if kid(x, "at") and any(
            abs(float(v) / 1.27 - round(float(v) / 1.27)) > 1e-3 for v in kid(x, "at")[1:3]))
        if off:
            add(WARN, "pins off 50 mil grid", p, f"{off}/{len(pins)}")
        uns = sum(1 for x in pins if x[1] == "unspecified")
        if uns:
            add(WARN, "unspecified pin type", p, str(uns))
        if p.ref in PASSIVE_REFS and len(pins) == 2 and any(x[1] != "passive" for x in pins):
            add(WARN, "2-pin passive with non-passive pin type", p)
        for x in pins:
            nm = (kid(x, "name") or ["", ""])[1].upper()
            if re.fullmatch(r"V?OUT|VO", nm) and x[1] == "power_in":
                add(WARN, "output pin typed power_in", p, nm)

    for p in parts:
        if seen.get(p.name, 0) > 1:
            add(ERR, "duplicate symbol name", p)
            seen[p.name] = 0
    used = {p.footprint for p in parts}
    for name in sorted(fps - used):
        out.append(Finding(WARN, "footprint not used by any symbol", "footprint", name))
    return out


def lint_footprints() -> list[Finding]:
    out: list[Finding] = []
    for f in sorted(os.listdir(FP_DIR)):
        if not f.endswith(".kicad_mod"):
            continue
        n = f[:-10]
        fp = load_footprint(n)

        def add(level, rule, detail=""):
            out.append(Finding(level, rule, "footprint", n, detail))

        if fp[1] != n:
            add(WARN, "internal footprint name differs from file name", fp[1])
        if re.search(r'[*?<>|:"]', n):
            add(ERR, "character not allowed in Windows file names")
        pads = kids(fp, "pad")
        types = {p[2] for p in pads}
        attr = (kid(fp, "attr") or [None, None])[1]
        if attr == "smd" and "thru_hole" in types:
            add(WARN, "attr smd but has plated THT pads (check .pos / assembly)")
        if attr == "through_hole" and "thru_hole" not in types and "smd" in types:
            add(ERR, "attr through_hole but all pads SMD (part missing from .pos file)")
        layers = {kid(x, "layer")[1] for x in walk(fp) if kid(x, "layer") and x[0].startswith("fp_")}
        if not any("CrtYd" in lay for lay in layers):
            add(WARN, "no courtyard")
        if not any("Fab" in lay for lay in layers):
            add(WARN, "no fab layer outline")
        if not any("${REFERENCE}" in x or "%R" in x for x in walk(fp)
                   if x[0] in ("fp_text", "property") and kid(x, "layer") and "Fab" in kid(x, "layer")[1]):
            add(WARN, "no ${REFERENCE} on F.Fab")
        if not kid(fp, "descr") or not kid(fp, "descr")[1].strip():
            add(WARN, "no footprint description")
        models = kids(fp, "model")
        if not models and pads and not n.startswith(VIRTUAL):
            add(WARN, "no 3D model")
        for m in models:
            path = m[1]
            if path.startswith("${ORIGINAL_COMPONENTS}/"):
                if not (ORIG_DIR / path.split("/", 1)[1]).exists():
                    add(ERR, "3D model file missing", path)
            elif not path.startswith("${KICAD"):
                add(ERR, "3D model path not portable", path)
    return out


def run(parts: list[Part] | None = None) -> list[Finding]:
    parts = parts if parts is not None else load_parts()
    return lint_symbols(parts) + lint_footprints()


def findings_by_part(parts: list[Part], findings: list[Finding]) -> dict[str, list[Finding]]:
    """Map each symbol name to its own findings plus those of its footprint."""
    by_fp: dict[str, list[Finding]] = {}
    out: dict[str, list[Finding]] = {p.name: [] for p in parts}
    for f in findings:
        if f.kind == "symbol":
            out.setdefault(f.item, []).append(f)
        else:
            by_fp.setdefault(f.item, []).append(f)
    for p in parts:
        out[p.name].extend(by_fp.get(p.footprint, []))
    return out