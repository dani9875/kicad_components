"""Add an LCSC part to pepy_sym_lib: fetch, clean, insert."""
from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .library import (CACHE_DIR, FP_DIR, LIB_NAME, ORIG_DIR, PASSIVE_REFS, ROOT, SYM_LIB,
                      category_of)
from .sexp import Str, dump, kid, kids, num, parse, walk


class AddError(Exception):
    pass


@dataclass
class AddResult:
    name: str
    symbol_mode: str = ""
    footprint: str = ""
    model: str = ""
    warnings: list[str] = field(default_factory=list)
    symbol_text: str = ""
    footprint_text: str = ""


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------
def fetch_easyeda(lcsc: str, work: Path, warn: Callable[[str], None]) -> dict:
    try:
        from easyeda2kicad.easyeda.easyeda_api import EasyedaApi
        from easyeda2kicad.easyeda.easyeda_importer import (
            Easyeda3dModelImporter, EasyedaFootprintImporter, EasyedaSymbolImporter)
        from easyeda2kicad.kicad.export_kicad_3d_model import Exporter3dModelKicad
        from easyeda2kicad.kicad.export_kicad_footprint import ExporterFootprintKicad
        from easyeda2kicad.kicad.export_kicad_symbol import ExporterSymbolKicad
    except ImportError as e:
        raise AddError("easyeda2kicad is missing. Run: pip install easyeda2kicad") from e

    cwd = os.getcwd()
    os.chdir(ROOT)  # EasyedaApi caches into ./.easyeda_cache
    try:
        api = EasyedaApi(use_cache=True)
        cad = api.get_cad_data_of_component(lcsc_id=lcsc)
    finally:
        os.chdir(cwd)
    if not cad:
        raise AddError(f"EasyEDA has no data for {lcsc}.")

    sym_path = work / "raw.kicad_sym"
    sym = EasyedaSymbolImporter(easyeda_cp_cad_data=cad).get_symbol()
    ExporterSymbolKicad(symbol=sym, lib_path=str(sym_path)).save_to_lib(
        lib_path=str(sym_path), footprint_lib_name=LIB_NAME, overwrite=True)

    fp = EasyedaFootprintImporter(easyeda_cp_cad_data=cad).get_footprint()
    fp_path = work / "raw.kicad_mod"
    ExporterFootprintKicad(footprint=fp).export(
        footprint_full_path=str(fp_path), model_3d_path="${ORIGINAL_COMPONENTS}")

    models: dict[str, Path] = {}
    m = Exporter3dModelKicad(model_3d=Easyeda3dModelImporter(
        easyeda_cp_cad_data=cad, download_raw_3d_model=True, api=api).output)
    if m.output:
        mdir = work / "3d"
        m.export(output_dir=str(mdir), overwrite=True)
        for ext in ("step", "wrl"):
            p = mdir / f"{m.output.name}.{ext}"
            if p.exists():
                models[ext] = p
    else:
        warn("EasyEDA has no 3D model for this part.")

    return {"sym": sym_path.read_text(encoding="utf-8"), "fp": fp_path.read_text(encoding="utf-8"),
            "models": models, "description": cad.get("description", "") or "",
            "tags": cad.get("tags", []) or []}


def fetch_lcsc(lcsc: str, warn: Callable[[str], None]) -> dict:
    """LCSC product data. Best effort: returns {} when unreachable."""
    url = f"https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={lcsc}"
    cache = CACHE_DIR / f"lcsc_{lcsc}.json"
    try:
        if cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode())
            CACHE_DIR.mkdir(exist_ok=True)
            cache.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        res = data.get("result") or {}
        params = {p.get("paramNameEn", ""): p.get("paramValueEn", "") for p in res.get("paramVOList") or []}
        return {"description": res.get("productIntroEn", "") or "", "datasheet": res.get("pdfUrl", "") or "",
                "manufacturer": res.get("brandNameEn", "") or "", "mpn": res.get("productModel", "") or "",
                "package": res.get("encapStandard", "") or "", "category": res.get("catalogName", "") or "",
                "params": params}
    except Exception as e:  # noqa: BLE001
        warn(f"LCSC data not available ({e.__class__.__name__}). Only EasyEDA data used.")
        return {}


# --------------------------------------------------------------------------
# Part data helpers
# --------------------------------------------------------------------------
def clean(v: str) -> str:
    """Strip whitespace and Chinese brand suffixes: 'UNI-ROYAL(厚声)' -> 'UNI-ROYAL'."""
    return re.sub(r"\s*\([^)]*[　-鿿][^)]*\)", "", v or "").strip()


def first(*vals: str) -> str:
    for v in vals:
        if v and clean(v):
            return clean(v)
    return ""


def eng_value(desc: str, params: dict, ref: str) -> str:
    """Value shown on the schematic (Nvalue). '' if not found."""
    keys = {"R": ("Resistance",), "C": ("Capacitance",), "L": ("Inductance",),
            "FB": ("Impedance @ Frequency", "Impedance")}.get(ref, ())
    for k in keys:
        if params.get(k):
            v = params[k].split("@")[0].strip().replace(" ", "")
            if ref in ("R", "FB"):
                v = re.sub(r"([kKmMG]?)Ω$", lambda m: (m.group(1).replace("K", "k") or "R"), v)
            return v
    pats = {"R": r"(\d+(?:\.\d+)?)\s*([mkKMG]?)Ω", "C": r"(\d+(?:\.\d+)?)\s*([pnuµm])F\b",
            "L": r"(\d+(?:\.\d+)?)\s*([nuµm])H\b", "FB": r"(\d+(?:\.\d+)?)\s*([kK]?)Ω"}
    m = re.search(pats.get(ref, r"$^"), desc)
    if not m:
        return ""
    n, pre = m.group(1), m.group(2).replace("µ", "u").replace("K", "k")
    if ref in ("R", "FB"):
        return n + (pre or "R")
    return n + pre + ("F" if ref == "C" else "H")


def tolerance(desc: str, params: dict) -> str:
    v = params.get("Tolerance", "").strip()
    if not v:
        m = re.search(r"±\s*(\d+(?:\.\d+)?)\s*%", desc)
        v = f"±{m.group(1)}%" if m else ""
    if v and not v.startswith("±"):
        v = "±" + v
    return v


def voltage(desc: str, params: dict) -> str:
    for k in ("Voltage Rated", "Voltage Rating", "Rated Voltage", "Voltage Rating (DC)"):
        if params.get(k):
            return params[k].strip()
    m = re.search(r"(?<![\w.±])(\d+(?:\.\d+)?)\s*V\b", desc)
    return f"{m.group(1)}V" if m else ""


def power(desc: str, params: dict) -> str:
    for k in ("Power(Watts)", "Power", "Power Rating"):
        if params.get(k):
            return params[k].strip()
    m = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(m?W)\b", desc)
    return f"{m.group(1)}{m.group(2)}" if m else ""


def dielectric(desc: str, params: dict) -> str:
    v = params.get("Temperature Coefficient", "")
    if re.fullmatch(r"X[5-8][RSTP]|C0G|NP0|Y5V|Z5U|X7T", v.strip()):
        return v.strip()
    m = re.search(r"\b(X[5-8][RSTP]|C0G|NP0|Y5V|Z5U)\b", desc)
    return m.group(1) if m else ""


def rated_current(desc: str, params: dict) -> str:
    for k in ("Rated Current", "Current Rating"):
        if params.get(k):
            return params[k].strip()
    return ""


# --------------------------------------------------------------------------
# Symbol
# --------------------------------------------------------------------------
FIELD_ORDER = ["Reference", "Value", "Footprint", "Datasheet", "Description",
               "Manufacturer", "LCSC Part", "Package", "Nvalue", "Tolerance",
               "Voltage rating", "Power", "Dielectric", "Rated current", "ki_keywords"]
ALWAYS_FIELDS = ("Reference", "Value", "Footprint", "Datasheet")

PIN_RULES = [
    (re.compile(r"^(A|D|P|S|E)?GND\d*$|^VSS[A-Z0-9_]*$|^V-$|^EP(AD)?$|^(E)?PAD$|^THERMAL", re.I), "power_in"),
    (re.compile(r"^(A|D|P)?V(CC|DD|DDA|DDIO|IN|BAT|BUS|S)[A-Z0-9_]*$|^V\+$", re.I), "power_in"),
    (re.compile(r"^N\.?C\.?\d*$|^DNC$", re.I), "no_connect"),
]


def prop_node(name: str, value: str, at, hide: bool) -> list:
    node = ["property", Str(name), Str(value), ["at", *map(num, at)],
            ["show_name", "no"], ["do_not_autoplace", "no"]]
    if hide:
        node.append(["hide", "yes"])
    node.append(["effects", ["font", ["size", "1.27", "1.27"]]])
    return node


def pins_of(sym) -> list:
    return [n for n in walk(sym) if n[0] == "pin"]


def pin_sig(sym) -> dict:
    out = {}
    for p in pins_of(sym):
        n = kid(p, "number")
        if n:
            out[n[1]] = ((kid(p, "name") or ["", ""])[1]).strip().upper()
    return out


def rename_units(sym, old: str, new: str) -> None:
    sym[1] = Str(new)
    for u in kids(sym, "symbol"):
        suffix = u[1][len(old):] if u[1].startswith(old) else "_" + "_".join(u[1].split("_")[-2:])
        u[1] = Str(new + suffix)


def modernise_raw(sym) -> None:
    """easyeda2kicad writes KiCad 6 syntax. Convert to KiCad 8+."""
    for n in list(walk(sym)):
        if n[0] == "effects" and "hide" in n:
            n.remove("hide")
        if n[0] == "pin" and "hide" in n:
            n.remove("hide")
            n.append(["hide", "yes"])
        for c in list(n):
            if isinstance(c, list) and c and c[0] == "id":
                n.remove(c)


def symbol_blocks(lib_text: str):
    """Yield (name, start, end) of each top-level symbol in the library text."""
    for m in re.finditer(r'^\t\(symbol "((?:\\.|[^"\\])*)"', lib_text, re.M):
        end = lib_text.find("\n\t)\n", m.start()) + 4
        yield m.group(1).replace('\\"', '"'), m.start(), end


def find_template(lib_text: str, category: str, sig: dict, ref: str):
    if not category:
        return None
    best = None
    for name, s, e in symbol_blocks(lib_text):
        if not name.startswith(category):
            continue
        node = parse(lib_text[s:e])[0]
        tsig = pin_sig(node)
        if set(tsig) != set(sig):
            continue
        names_match = all(tsig[k] == sig[k] for k in sig)
        passive_2pin = len(sig) == 2 and ref in PASSIVE_REFS and not any(tsig.values())
        if names_match or passive_2pin:
            best = node
    return best


def fix_pin_types(sym, ref: str, warn) -> None:
    pins = pins_of(sym)
    if len(pins) <= 2 and ref in PASSIVE_REFS:
        for p in pins:
            p[1] = "passive"
        return
    if len(pins) <= 2 and all(p[1] == "unspecified" for p in pins):
        for p in pins:
            p[1] = "passive"
        return
    for p in pins:
        if p[1] != "unspecified":
            continue
        nm = (kid(p, "name") or ["", ""])[1].strip()
        for rx, t in PIN_RULES:
            if rx.search(nm):
                p[1] = t
                break
    left = sum(1 for p in pins if p[1] == "unspecified")
    if left:
        warn(f"{left} pin(s) still 'unspecified'. Set input/output/bidirectional for ERC.")


def check_symbol(sym, ref: str, warn) -> None:
    pins = pins_of(sym)
    off = sum(1 for p in pins if kid(p, "at") and any(
        abs(float(v) / 1.27 - round(float(v) / 1.27)) > 1e-3 for v in kid(p, "at")[1:3]))
    if off:
        warn(f"{off} pin(s) off the 50 mil grid. Wires will not connect cleanly.")
    nums = [kid(p, "number")[1] for p in pins if kid(p, "number")]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    if dup:
        warn(f"Duplicate pin numbers {dup[:6]}. Make sure they are stacked or in separate units.")
    if ref == "U" and len(nums) <= 3:
        warn("Reference is U on a part with 3 pins or fewer. Check the prefix (Q/D/...).")


def build_symbol(raw_text: str, name: str, footprint: str, data: dict, lib_text: str, warn):
    raw = kids(parse(raw_text)[0], "symbol")[0]
    raw_props = {p[1]: p[2] for p in kids(raw, "property")}
    ref = raw_props.get("Reference", "U").rstrip("?") or "U"
    category = category_of(name)
    sig = pin_sig(raw)

    tmpl = find_template(lib_text, category, sig, ref)
    if tmpl is not None:
        sym = copy.deepcopy(tmpl)
        rename_units(sym, tmpl[1], name)
        tprops = {p[1]: p for p in kids(sym, "property")}
        ref = tprops["Reference"][2] if "Reference" in tprops else ref
        mode = f"cloned from {tmpl[1]}"
    else:
        sym = copy.deepcopy(raw)
        modernise_raw(sym)
        rename_units(sym, raw[1], name)
        for p in kids(sym, "property") + kids(sym, "in_bom") + kids(sym, "on_board"):
            sym.remove(p)
        tprops = {}
        mode = "converted from EasyEDA"
        fix_pin_types(sym, ref, warn)

    desc, params = data["description"], data["params"]
    passive = ref in ("R", "C", "L", "FB") or "Nvalue" in tprops
    values = {
        "Reference": ref, "Value": data["mpn"], "Footprint": footprint,
        "Datasheet": data["datasheet"], "Description": desc,
        "Manufacturer": data["manufacturer"], "LCSC Part": data["lcsc"], "Package": data["package"],
        "ki_keywords": " ".join(dict.fromkeys(
            x for x in [category.strip("[]").replace("_", " ")] + data["tags"] + [data["category"]] if x)).strip(),
    }
    if passive:
        values["Nvalue"] = eng_value(desc, params, ref) or "?"
        if values["Nvalue"] == "?":
            warn("Could not read the component value. Set Nvalue by hand.")
        values["Tolerance"] = tolerance(desc, params)
        if ref in ("C",) or "Voltage rating" in tprops:
            values["Voltage rating"] = voltage(desc, params)
        if ref == "R":
            values["Power"] = power(desc, params)
        if ref == "C":
            values["Dielectric"] = dielectric(desc, params)
        if ref in ("L", "FB"):
            values["Rated current"] = rated_current(desc, params)
        for k in ("Tolerance", "Voltage rating"):
            if k in values and not values[k]:
                warn(f"{k} not found in LCSC data. Field left out.")

    # Drop template fields with no data for this part (no stale values from the template).
    for k, p in list(tprops.items()):
        if not values.get(k) and k not in ALWAYS_FIELDS and k != "ki_locked":
            sym.remove(p)
            del tprops[k]

    y = -6.35
    insert_at = next(i for i, c in enumerate(sym) if isinstance(c, list) and c[0] == "symbol")
    visible = {"Reference", "Nvalue"} | (set() if passive else {"Value"})
    for k in FIELD_ORDER:
        v = values.get(k, "")
        if not v and k not in ALWAYS_FIELDS:
            continue
        if k in tprops:
            tprops[k][2] = Str(v)
        else:
            at = {"Reference": (0, 2.54, 0), "Value": (0, -2.54, 0), "Nvalue": (0, -2.54, 0)}.get(k)
            if at is None:
                at = (0, y, 0)
                y -= 2.54
            sym.insert(insert_at, prop_node(k, v, at, hide=k not in visible))
            insert_at += 1

    if tmpl is None:
        head = [["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"],
                ["in_pos_files", "yes"], ["duplicate_pin_numbers_are_jumpers", "no"]]
        if len(sig) <= 2:
            head.insert(0, ["pin_numbers", ["hide", "yes"]])
            head.insert(1, ["pin_names", ["hide", "yes"]])
        for h in reversed(head):
            sym.insert(2, h)
        if not kid(sym, "embedded_fonts"):
            sym.append(["embedded_fonts", "no"])

    check_symbol(sym, ref, warn)
    return sym, mode


# --------------------------------------------------------------------------
# Footprint
# --------------------------------------------------------------------------
def courtyard_lines(fp, margin: float) -> list:
    pts = []
    for p in kids(fp, "pad"):
        at, size = kid(p, "at"), kid(p, "size")
        if not at or not size:
            continue
        x, y = float(at[1]), float(at[2])
        r = float(at[3]) % 180 if len(at) > 3 else 0
        w, h = float(size[1]), float(size[2])
        if abs(r - 90) < 1:
            w, h = h, w
        elif r > 1:
            w = h = math.hypot(w, h)
        pts += [(x - w / 2, y - h / 2), (x + w / 2, y + h / 2)]
    for n in walk(fp):
        lay = kid(n, "layer")
        if n[0] in ("fp_line", "fp_rect", "fp_arc") and lay and lay[1] in ("F.Fab", "F.SilkS"):
            for k in ("start", "end", "mid"):
                c = kid(n, k)
                if c:
                    pts.append((float(c[1]), float(c[2])))
    if not pts:
        return []
    g = 0.01
    x0 = math.floor((min(p[0] for p in pts) - margin) / g) * g
    y0 = math.floor((min(p[1] for p in pts) - margin) / g) * g
    x1 = math.ceil((max(p[0] for p in pts) + margin) / g) * g
    y1 = math.ceil((max(p[1] for p in pts) + margin) / g) * g
    return [["fp_line", ["start", num(a[0]), num(a[1])], ["end", num(b[0]), num(b[1])],
             ["layer", "F.CrtYd"], ["width", "0.05"]]
            for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                         ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0)))]


def build_footprint(raw_text: str, name: str, model_rel: str | None, data: dict, warn):
    fp = parse(raw_text)[0]
    fp[1] = Str(name)
    pads = kids(fp, "pad")
    tht = any(p[2] == "thru_hole" for p in pads)
    attr = kid(fp, "attr")
    if attr:
        attr[1] = "through_hole" if tht else "smd"
    else:
        fp.append(["attr", "through_hole" if tht else "smd"])

    for t in kids(fp, "fp_text"):
        if t[1] == "value":
            t[2] = Str(name)

    for n in kids(fp, "descr") + kids(fp, "tags"):
        fp.remove(n)
    i = 2
    while i < len(fp) and isinstance(fp[i], list) and fp[i][0] in (
            "version", "generator", "generator_version", "layer", "tedit"):
        i += 1
    fp.insert(i, ["descr", Str(f"{data['description']} {data['mpn']}, LCSC {data['lcsc']}".strip())])
    fp.insert(i + 1, ["tags", Str(" ".join(x for x in (data["package"], data["mpn"]) if x))])

    if not any((n[0] == "fp_text" and "%R" in n) or
               (n[0] in ("fp_text", "property") and "${REFERENCE}" in n and "F.Fab" in str(kid(n, "layer")))
               for n in walk(fp)):
        fp.append(["fp_text", "user", "${REFERENCE}", ["at", "0", "0"], ["layer", "F.Fab"],
                   ["effects", ["font", ["size", "1", "1"], ["thickness", "0.15"]]]])

    if not any(n[0].startswith("fp_") and kid(n, "layer") and "CrtYd" in kid(n, "layer")[1] for n in walk(fp)):
        fp.extend(courtyard_lines(fp, 0.5 if tht else 0.25))
        warn("Footprint had no courtyard. Added a rectangle; check it covers the body.")

    for m in kids(fp, "model"):
        fp.remove(m)
    if model_rel:
        fp.append(["model", Str("${ORIGINAL_COMPONENTS}/" + model_rel),
                   ["offset", ["xyz", "0", "0", "0"]], ["scale", ["xyz", "1", "1", "1"]],
                   ["rotate", ["xyz", "0", "0", "0"]]])
    return fp


def kicad_cli_upgrade_fp(fp_file: Path, warn) -> None:
    cli = shutil.which("kicad-cli")
    if not cli:
        return
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.pretty", Path(td) / "out.pretty"
        src.mkdir()
        shutil.copy(fp_file, src / fp_file.name)
        r = subprocess.run([cli, "fp", "upgrade", "--force", "-o", str(dst), str(src)],
                           capture_output=True, text=True)
        out = dst / fp_file.name
        if r.returncode == 0 and out.exists():
            shutil.copy(out, fp_file)
        else:
            warn("kicad-cli could not upgrade the footprint. Legacy format kept (KiCad still loads it).")


# --------------------------------------------------------------------------
# Library insertion
# --------------------------------------------------------------------------
def insert_symbol(lib_text: str, sym_text: str, name: str, overwrite: bool) -> str:
    blocks = list(symbol_blocks(lib_text))
    for n, s, e in blocks:
        if n == name:
            if not overwrite:
                raise AddError(f"Symbol already in library: {name}. Use overwrite to replace it.")
            return lib_text[:s] + sym_text + "\n" + lib_text[e:]
    for n, s, _ in blocks:
        if n.lower() > name.lower():
            return lib_text[:s] + sym_text + "\n" + lib_text[s:]
    pos = lib_text.rstrip().rfind(")")
    return lib_text[:pos] + sym_text + "\n" + lib_text[pos:]


# --------------------------------------------------------------------------
def add_part(lcsc: str, name: str, generic: str | None = None, overwrite: bool = False,
             dry_run: bool = False, log: Callable[[str], None] = print,
             fetch_ee=fetch_easyeda, fetch_lc=fetch_lcsc) -> AddResult:
    name = name.strip()
    lcsc = lcsc.strip().upper()
    if not re.fullmatch(r"C\d+", lcsc):
        raise AddError("LCSC number must look like C12345.")
    if re.search(r'[/\\*?<>|:"]', name):
        raise AddError('Name must not contain / \\ * ? < > | : "')

    res = AddResult(name=name)
    warn = res.warnings.append
    category = category_of(name)
    if not generic and (ORIG_DIR / category / f"{category}.kicad_mod").exists():
        generic = category

    log(f"Fetching {lcsc} from EasyEDA ...")
    lib_text = SYM_LIB.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as td:
        ee = fetch_ee(lcsc, Path(td), warn)
        log("Fetching LCSC data ...")
        lc = fetch_lc(lcsc, warn)

        raw_props = {p[1]: p[2] for p in kids(kids(parse(ee["sym"])[0], "symbol")[0], "property")}
        data = {
            "lcsc": lcsc,
            "mpn": first(lc.get("mpn", ""), raw_props.get("MPN", ""), raw_props.get("Value", "")),
            "manufacturer": first(lc.get("manufacturer", ""), raw_props.get("Manufacturer", "")),
            "datasheet": first(lc.get("datasheet", ""), raw_props.get("Datasheet", "")),
            "description": first(lc.get("description", ""), ee["description"],
                                 raw_props.get("Description", ""), raw_props.get("ki_description", "")),
            "package": first(lc.get("package", "")),
            "category": lc.get("category", ""),
            "params": lc.get("params", {}),
            "tags": ee["tags"] + [raw_props.get("ki_keywords", "")],
        }
        if not data["description"]:
            warn("No description available. Add one in the symbol editor.")

        log("Building symbol and footprint ...")
        fp_name = generic or name
        sym, res.symbol_mode = build_symbol(ee["sym"], name, f"{LIB_NAME}:{fp_name}", data, lib_text, warn)
        res.symbol_text = dump(sym, 1)
        res.footprint = f"{LIB_NAME}:{fp_name}" + (" (shared)" if generic else "")

        model_rel = None
        if not generic:
            if (FP_DIR / f"{name}.kicad_mod").exists() and not overwrite:
                raise AddError(f"Footprint already exists: {name}. Use overwrite.")
            for ext in ("step", "wrl"):
                if ext in ee["models"]:
                    model_rel = f"{name}/{name}.{ext}"
                    break
            res.footprint_text = dump(build_footprint(ee["fp"], name, model_rel, data, warn)) + "\n"
        res.model = model_rel or ("shared" if generic else "none")

        new_lib = insert_symbol(lib_text, res.symbol_text, name, overwrite)
        if dry_run:
            log("Dry run: nothing written.")
            return res

        dest = ORIG_DIR / (generic or name)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f"{name}.kicad_sym").write_text(ee["sym"], encoding="utf-8")  # raw, for provenance
        if res.footprint_text:
            (dest / f"{name}.kicad_mod").write_text(ee["fp"], encoding="utf-8")
            for ext, p in ee["models"].items():
                shutil.copy(p, dest / f"{name}.{ext}")
            fp_file = FP_DIR / f"{name}.kicad_mod"
            fp_file.write_text(res.footprint_text, encoding="utf-8")
            kicad_cli_upgrade_fp(fp_file, warn)
        tmp = SYM_LIB.with_suffix(".tmp")
        tmp.write_text(new_lib, encoding="utf-8")
        os.replace(tmp, SYM_LIB)
    log(f"Added {name}")
    return res
