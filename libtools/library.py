"""Library paths, part model and loaders."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .sexp import fnum, kid, kids, parse, walk

ROOT = Path(__file__).resolve().parent.parent
LIB_NAME = "pepy_sym_lib"
SYM_LIB = ROOT / f"{LIB_NAME}.kicad_sym"
FP_DIR = ROOT / f"{LIB_NAME}.pretty"
ORIG_DIR = ROOT / "original_components"
CACHE_DIR = ROOT / ".easyeda_cache"

PASSIVE_REFS = ("R", "C", "L", "FB", "F")
INTERNAL_FIELDS = {"ki_locked", "ki_fp_filters"}


def category_of(name: str) -> str:
    return name[: name.index("]") + 1] if name.startswith("[") and "]" in name else ""


@dataclass
class Part:
    name: str
    node: list
    fields: dict = field(default_factory=dict)

    @property
    def category(self) -> str:
        return category_of(self.name)

    @property
    def ref(self) -> str:
        return self.fields.get("Reference", "").rstrip("?")

    @property
    def footprint(self) -> str:
        """Footprint name inside pepy_sym_lib, or '' if none / other library."""
        fp = self.fields.get("Footprint", "")
        return fp.split(":", 1)[1] if fp.startswith(LIB_NAME + ":") else ""

    @property
    def pins(self) -> list:
        return [n for n in walk(self.node) if n[0] == "pin"]


def load_parts(path: Path = SYM_LIB) -> list[Part]:
    lib = parse(path.read_text(encoding="utf-8"))[0]
    parts = []
    for s in kids(lib, "symbol"):
        f = {p[1]: p[2] for p in kids(s, "property") if p[1] not in INTERNAL_FIELDS}
        parts.append(Part(name=s[1], node=s, fields=f))
    return parts


_fp_cache: dict[str, tuple[float, list]] = {}


def load_footprint(name: str):
    p = FP_DIR / f"{name}.kicad_mod"
    if not p.exists():
        return None
    mt = p.stat().st_mtime
    hit = _fp_cache.get(name)
    if hit and hit[0] == mt:
        return hit[1]
    node = parse(p.read_text(encoding="utf-8"))[0]
    _fp_cache[name] = (mt, node)
    return node


# --------------------------------------------------------------------------
# Engineering values (for sorting Nvalue numerically)
# --------------------------------------------------------------------------
_PREFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "": 1,
           "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "R": 1}


def eng_to_float(s: str) -> float | None:
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([pnuµmkKMGR]?)(\d*)", s or "")
    if not m:
        return None
    v = float(m.group(1))
    if m.group(3):  # 4k7 style
        v = float(f"{m.group(1)}.{m.group(3)}")
    return v * _PREFIX.get(m.group(2), 1)


# --------------------------------------------------------------------------
# Drawing primitives for the preview widgets
#   ("line",  [(x,y),...], kind)         open polyline
#   ("poly",  [(x,y),...], kind, filled) closed polygon
#   ("circle", (cx,cy), r, kind, filled)
#   ("pad",   shape, (cx,cy), (w,h), angle, kind, drill, number)
#   ("text",  (x,y), text, size, kind)
# Symbol coordinates are y-up, footprint coordinates are y-down.
# --------------------------------------------------------------------------
def arc_points(p0, pm, p1, steps: int = 24):
    (x1, y1), (x2, y2), (x3, y3) = p0, pm, p1
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        return [p0, p1]
    ux = ((x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1) + (x3**2 + y3**2) * (y1 - y2)) / d
    uy = ((x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3) + (x3**2 + y3**2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    a0, am, a1 = (math.atan2(y - uy, x - ux) for x, y in (p0, pm, p1))

    def ccw(a, b):
        return (b - a) % (2 * math.pi)
    sweep = ccw(a0, a1) if ccw(a0, am) <= ccw(a0, a1) else -ccw(a1, a0)
    return [(ux + r * math.cos(a0 + sweep * i / steps), uy + r * math.sin(a0 + sweep * i / steps))
            for i in range(steps + 1)]


def _xy(n, key):
    c = kid(n, key)
    return (fnum(c, 1), fnum(c, 2)) if c else None


def symbol_primitives(node) -> list:
    """Unit 1 (plus common unit 0), body style 1."""
    out = []
    hide_names = any(n[0] == "pin_names" and ("hide" in n or kid(n, "hide")) for n in node if isinstance(n, list))
    hide_nums = any(n[0] == "pin_numbers" and ("hide" in n or kid(n, "hide")) for n in node if isinstance(n, list))
    units = []
    for u in kids(node, "symbol"):
        m = re.search(r"_(\d+)_(\d+)$", u[1])
        if m and m.group(1) in ("0", "1") and m.group(2) in ("0", "1"):
            units.append(u)
    if not units:
        units = kids(node, "symbol")[:1]
    for u in units:
        for n in u:
            if not isinstance(n, list):
                continue
            filled = (kid(kid(n, "fill") or [], "type") or [None, "none"])[1] in ("background", "outline")
            t = n[0]
            if t == "rectangle":
                (x0, y0), (x1, y1) = _xy(n, "start"), _xy(n, "end")
                out.append(("poly", [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "body", filled))
            elif t == "polyline":
                pts = [(fnum(p, 1), fnum(p, 2)) for p in kids(kid(n, "pts") or [], "xy")]
                closed = filled and len(pts) > 2
                out.append(("poly", pts, "body", True) if closed else ("line", pts, "body"))
            elif t == "circle":
                out.append(("circle", _xy(n, "center"), fnum(kid(n, "radius"), 1), "body", filled))
            elif t == "arc":
                if kid(n, "mid"):
                    out.append(("line", arc_points(_xy(n, "start"), _xy(n, "mid"), _xy(n, "end")), "body"))
            elif t == "bezier":
                pts = [(fnum(p, 1), fnum(p, 2)) for p in kids(kid(n, "pts") or [], "xy")]
                out.append(("line", pts, "body"))
            elif t == "text":
                at = kid(n, "at")
                out.append(("text", (fnum(at, 1), fnum(at, 2)), n[1], 1.27, "text"))
            elif t == "pin":
                if kid(n, "hide") or "hide" in n:
                    continue
                at = kid(n, "at")
                x, y, a = fnum(at, 1), fnum(at, 2), math.radians(fnum(at, 3))
                ln = fnum(kid(n, "length"), 1, 2.54)
                ex, ey = x + ln * math.cos(a), y + ln * math.sin(a)
                out.append(("line", [(x, y), (ex, ey)], "pin"))
                pname = (kid(n, "name") or ["", ""])[1]
                pnum = (kid(n, "number") or ["", ""])[1]
                if pname and pname != "~" and not hide_names:
                    ox, oy = ex + 0.6 * math.cos(a), ey + 0.6 * math.sin(a)
                    out.append(("text", (ox, oy), re.sub(r"~\{([^}]*)\}", r"\1", pname), 1.0,
                                "pinname", math.degrees(a)))
                if pnum and not hide_nums:
                    mx, my = (x + ex) / 2, (y + ey) / 2
                    out.append(("text", (mx, my + 0.6), pnum, 0.8, "pinnum"))
    return out


_FP_LAYERS = {"F.SilkS": "silk", "F.Fab": "fab", "F.CrtYd": "crtyd", "Edge.Cuts": "edge",
              "B.SilkS": "bsilk", "B.Fab": "bfab", "B.CrtYd": "bcrtyd"}


def footprint_primitives(node) -> list:
    out = []
    for n in node:
        if not isinstance(n, list) or not n:
            continue
        t = n[0]
        lay = kid(n, "layer")
        kind = _FP_LAYERS.get(lay[1] if lay else "", None)
        if t in ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly") and kind:
            filled = (kid(n, "fill") or [None, "none"])[1] in ("solid", "yes")
            if t == "fp_line":
                out.append(("line", [_xy(n, "start"), _xy(n, "end")], kind))
            elif t == "fp_rect":
                (x0, y0), (x1, y1) = _xy(n, "start"), _xy(n, "end")
                out.append(("poly", [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], kind, filled))
            elif t == "fp_circle":
                c, e = _xy(n, "center"), _xy(n, "end")
                out.append(("circle", c, math.hypot(e[0] - c[0], e[1] - c[1]), kind, filled))
            elif t == "fp_arc":
                if kid(n, "mid"):
                    out.append(("line", arc_points(_xy(n, "start"), _xy(n, "mid"), _xy(n, "end")), kind))
                elif kid(n, "angle"):  # legacy: start=center, end=arc start
                    (cx, cy), (sx, sy) = _xy(n, "start"), _xy(n, "end")
                    ang = math.radians(fnum(kid(n, "angle"), 1))
                    r, a0 = math.hypot(sx - cx, sy - cy), math.atan2(sy - cy, sx - cx)
                    out.append(("line", [(cx + r * math.cos(a0 + ang * i / 24),
                                          cy + r * math.sin(a0 + ang * i / 24)) for i in range(25)], kind))
            elif t == "fp_poly":
                pts = [(fnum(p, 1), fnum(p, 2)) for p in kids(kid(n, "pts") or [], "xy")]
                out.append(("poly", pts, kind, filled))
        elif t == "pad":
            at, size = kid(n, "at"), kid(n, "size")
            if not at or not size:
                continue
            layers = " ".join(str(x) for x in (kid(n, "layers") or [])[1:])
            pk = "pad_b" if "B.Cu" in layers and "F.Cu" not in layers and "*.Cu" not in layers else "pad"
            if n[2] == "np_thru_hole":
                pk = "npth"
            drill = kid(n, "drill")
            d = 0.0
            if drill:
                nums = [x for x in drill[1:] if not isinstance(x, list) and x != "oval"]
                d = float(nums[0]) if nums else 0.0
            out.append(("pad", str(n[3]), (fnum(at, 1), fnum(at, 2)), (fnum(size, 1), fnum(size, 2)),
                        fnum(at, 3), pk, d, str(n[1])))
    return out
