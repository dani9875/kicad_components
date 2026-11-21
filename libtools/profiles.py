"""Column profiles: which fields matter for which Group.

Each Group lists its own fields. Nothing is inherited; the same field name
("Voltage rating", "Rated current", ...) may appear in several profiles.

A Spec says:
  field   symbol field name (hidden property in the symbol)
  label   short label in the combined "Specs" column ("" = value only)
  params  regexes matched against LCSC parameter names (paramNameEn)
  numeric sort by engineering value (10k < 100k) instead of text

Edit this file to add groups or fields. The browser, fill_specs.py and
add_component.py all read it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Spec:
    field: str
    label: str = ""
    params: tuple[str, ...] = ()
    numeric: bool = True


def S(field, label="", *params, numeric=True):
    return Spec(field, label, tuple(params), numeric)


PROFILES: dict[str, list[Spec]] = {
    "Resistors": [
        S("Nvalue", "", r"^Resistance$"),
        S("Tolerance", "", r"^Tolerance$"),
        S("Power", "", r"^Power"),
        S("Voltage rating", "", r"^Voltage Rating", r"Overload Voltage"),
        S("TCR", "", r"^Temperature Coefficient", numeric=False),
    ],
    "Capacitors": [
        S("Nvalue", "", r"^Capacitance$"),
        S("Voltage rating", "", r"^Voltage Rat"),
        S("Dielectric", "", r"^Temperature Coefficient", numeric=False),
        S("Tolerance", "", r"^Tolerance$"),
    ],
    "Inductors": [
        S("Nvalue", "", r"^Inductance$"),
        S("Rated current", "Ir", r"^Current Rating", r"^Rated Current"),
        S("Saturation current", "Isat", r"Saturation"),
        S("DCR", "DCR", r"^DC Resistance"),
        S("Tolerance", "", r"^Tolerance$"),
    ],
    "Ferrite beads": [
        S("Impedance", "", r"^Impedance"),
        S("Rated current", "Ir", r"^Current Rating", r"^Rated Current"),
        S("DCR", "DCR", r"^DC Resistance"),
    ],
    "Filters": [
        S("Impedance", "", r"^Impedance", r"Common Mode"),
        S("Rated current", "Ir", r"^Current Rating", r"^Rated Current"),
        S("DCR", "DCR", r"^DC Resistance"),
    ],
    "ESD / TVS": [
        S("Standoff voltage", "Vrwm", r"Stand-?Off"),
        S("Breakdown voltage", "Vbr", r"Breakdown"),
        S("Clamping voltage", "Vc", r"^Clamping"),
        S("Peak power", "Ppp", r"Peak Pulse Power"),
        S("Channels", "ch", r"Number of Channels"),
        S("Polarity", "", r"^Polarity", numeric=False),
    ],
    "Diodes": [
        S("Reverse voltage", "Vr", r"DC Reverse", r"^Reverse Voltage", r"Reverse.*\(Vr\)"),
        S("Forward current", "If", r"Rectified", r"^Forward Current", r"Average.*Current"),
        S("Forward voltage", "Vf", r"Voltage - Forward", r"^Forward Voltage"),
    ],
    "Transistors": [
        S("Vds", "Vds", r"Drain to Source Voltage", r"Drain-Source Voltage"),
        S("Id", "Id", r"Continuous Drain", r"^Current - Collector"),
        S("Rds(on)", "Rds", r"^RDS\(on\)", r"On-Resistance"),
        S("Vgs(th)", "Vth", r"Gate Threshold"),
    ],
    "LDOs": [
        S("Vout", "Vout", r"^Output Voltage"),
        S("Iout", "Iout", r"^Output Current"),
        S("Vin max", "Vin", r"^Operating Voltage", r"^Input Voltage", r"^Voltage - Input"),
        S("Dropout", "Vdo", r"Dropout"),
        S("Iq", "Iq", r"\(Iq\)", r"Quiescent", r"^Supply Current"),
    ],
    "DC/DC converters": [
        S("Topology", "", r"^Topology", r"^Function", numeric=False),
        S("Vin", "Vin", r"^Operating Voltage", r"^Input Voltage", r"^Voltage - Input"),
        S("Vout", "Vout", r"^Output Voltage"),
        S("Iout", "Iout", r"^Output Current", r"^Current - Output"),
        S("Switching frequency", "fsw", r"Switching"),
    ],
    "LEDs & displays": [
        S("Color", "", r"Illumination Color", r"Emitted Color", r"^Color$", numeric=False),
        S("Forward voltage", "Vf", r"Voltage - Forward", r"^Forward Voltage"),
        S("Forward current", "If", r"^Forward Current", r"^Current - Test"),
        S("Luminous intensity", "", r"Luminous Intensity"),
    ],
    "Crystals & timing": [
        S("Frequency", "", r"^Frequency$"),
        S("Frequency tolerance", "", r"Frequency (Tolerance|Stability)", numeric=False),
        S("Load capacitance", "CL", r"Load Capacitance"),
        S("Supply voltage", "Vcc", r"Voltage - Supply", r"^Supply Voltage"),
    ],
}

SPECS = "Specs"


def profile(group: str) -> list[Spec]:
    return PROFILES.get(group, [])


def all_fields() -> list[str]:
    """Every profile field once, in first-seen order."""
    out: list[str] = []
    for specs in PROFILES.values():
        for s in specs:
            if s.field not in out:
                out.append(s.field)
    return out


def numeric_fields() -> set[str]:
    return {s.field for specs in PROFILES.values() for s in specs if s.numeric}


def specs_text(group: str, fields: dict) -> str:
    """'1uF · 6.3V · X5R · ±20%' from the part's own profile."""
    bits = []
    for s in profile(group):
        v = (fields.get(s.field) or "").strip()
        if v:
            bits.append(f"{v} {s.label}" if s.label else v)
    return " · ".join(bits)


# --------------------------------------------------------------------------
# Values from LCSC parameters
# --------------------------------------------------------------------------
_WORDS = {"single": "1", "one": "1", "dual": "2", "two": "2", "three": "3", "four": "4",
          "five": "5", "six": "6", "eight": "8", "ten": "10", "twelve": "12", "sixteen": "16"}


def clean(field: str, v: str) -> str:
    v = (v or "").strip()
    if v in ("", "-", "--", "N/A", "null"):
        return ""
    if field == "Channels":
        m = re.match(r"(\d+)", v)
        if m:
            return m.group(1)
        w = re.match(r"([A-Za-z]+)", v)
        return _WORDS.get(w.group(1).lower(), v) if w else v
    if field in ("Rds(on)", "Peak power", "Vgs(th)", "Forward voltage"):
        v = v.split(";")[0]
        if field in ("Rds(on)", "Peak power", "Vgs(th)"):
            v = v.split("@")[0]
    if field == "Tolerance" and re.match(r"^\d", v):
        v = "±" + v
    if field == "Nvalue":
        v = v.replace("Ω", "").replace("kR", "k") if re.search(r"Ω$", v) else v
        if re.fullmatch(r"\d+(\.\d+)?", v):
            v += "R"
    return v.replace("℃", "°C")


def extract(group: str, params: dict) -> dict:
    """field -> value for the group's profile, from LCSC paramNameEn -> paramValueEn."""
    out = {}
    for s in profile(group):
        for rx in s.params:
            hit = next((v for k, v in params.items() if re.search(rx, k or "", re.I)), None)
            if hit is not None:
                val = clean(s.field, hit)
                if val:
                    out[s.field] = val
                    break
    return out


def from_description(group: str, desc: str) -> dict:
    """Offline fallback for passives: values that are unambiguous in a description."""
    out = {}
    d = desc or ""
    if group in ("Resistors", "Capacitors", "Inductors"):
        m = re.search(r"±\s*(\d+(?:\.\d+)?)\s*%", d)
        if m:
            out["Tolerance"] = f"±{m.group(1)}%"
    if group == "Resistors":
        f = re.search(r"(?<![\w.])(\d+)/(\d+)\s*W\b", d)            # 1/16W
        m = re.search(r"(?<![\w./])(\d+(?:\.\d+)?)\s*(mW|W)\b", d)
        if f:
            mw = 1000 * int(f.group(1)) / int(f.group(2))
            out["Power"] = f"{mw:g}mW" if mw < 1000 else f"{mw / 1000:g}W"
        elif m:
            out["Power"] = m.group(1) + m.group(2)
        volts = set(re.findall(r"(?<![\w.±~])(\d+(?:\.\d+)?)\s*V\b", d))
        if len(volts) == 1:
            out["Voltage rating"] = volts.pop() + "V"
        m = re.search(r"±\s*(\d+)\s*ppm", d)
        if m:
            out["TCR"] = f"±{m.group(1)}ppm/°C"
    if group == "Capacitors":
        volts = set(re.findall(r"(?<![\w.±~])(\d+(?:\.\d+)?)\s*[kK]?V\b", d))
        kv = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)\s*kV\b", d)
        if kv:
            out["Voltage rating"] = kv.group(1) + "kV"
        elif len(volts) == 1:
            out["Voltage rating"] = volts.pop() + "V"
        m = re.search(r"\b(X[5-8][RSTP]|C0G|NP0|COG|Y5V|Z5U)\b", d)
        if m:
            out["Dielectric"] = m.group(1).replace("COG", "C0G")
    if group == "Inductors":
        m = re.search(r"(\d+(?:\.\d+)?)\s*mΩ", d)
        if m:
            out["DCR"] = m.group(1) + "mΩ"
    return out