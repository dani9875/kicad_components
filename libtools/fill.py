"""Write profile fields into symbols of pepy_sym_lib.kicad_sym."""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from . import profiles
from .add import fetch_lcsc, prop_node, symbol_blocks
from .library import CACHE_DIR, SYM_LIB, category_of, group_of
from .sexp import Str, dump, fnum, kid, kids, parse


def set_fields(block: str, values: dict, overwrite: bool = False) -> tuple[str, dict]:
    """Return (new block text, {field: (old, new)}). Only changed blocks are re-serialised."""
    node = parse(block)[0]
    props = {p[1]: p for p in kids(node, "property")}
    changed = {}
    ys = [fnum(kid(p, "at"), 2) for p in props.values() if kid(p, "at")]
    y = (min(ys) if ys else 0) - 2.54
    insert_at = next((i for i, c in enumerate(node) if isinstance(c, list) and c[0] == "symbol"), len(node))
    for k, v in values.items():
        if not v:
            continue
        p = props.get(k)
        if p is not None:
            old = p[2]
            if (old.strip() and not overwrite) or old == v:
                continue
            p[2] = Str(v)
            changed[k] = (old, v)
        else:
            node.insert(insert_at, prop_node(k, v, (0, y, 0), hide=True))
            insert_at += 1
            y -= 2.54
            changed[k] = ("", v)
    if not changed:
        return block, {}
    return dump(node, 1) + "\n", changed


@dataclass
class FillReport:
    parts: int = 0
    updated: list[str] = field(default_factory=list)
    filled: dict[str, int] = field(default_factory=dict)     # field -> count
    still_missing: dict[str, list[str]] = field(default_factory=dict)
    no_lcsc: list[str] = field(default_factory=list)
    fetch_failed: list[str] = field(default_factory=list)
    changes: dict[str, dict] = field(default_factory=dict)   # part -> {field: (old, new)}


def fill_library(only: str = "", overwrite: bool = False, offline: bool = False, dry_run: bool = False,
                 log: Callable[[str], None] = print, delay: float = 0.25) -> FillReport:
    text = SYM_LIB.read_text(encoding="utf-8")
    rep = FillReport()
    blocks = [(n, a, b) for n, a, b in symbol_blocks(text) if n.startswith(only)]
    out, pos = [], 0
    for i, (name, a, b) in enumerate(blocks, start=1):
        group = group_of(category_of(name))
        specs = profiles.profile(group)
        if not specs:
            continue
        rep.parts += 1
        block = text[a:b]
        node = parse(block)[0]
        fields = {p[1]: p[2] for p in kids(node, "property")}
        need = [s.field for s in specs if overwrite or not fields.get(s.field, "").strip()]
        tol_fix = bool(re.fullmatch(r"\d+(\.\d+)?\s*%", fields.get("Tolerance", "").strip()))
        if not need and not tol_fix:
            continue
        values: dict[str, str] = {}
        lc = fields.get("LCSC Part", "").strip()
        if lc and not offline:
            cached = (CACHE_DIR / f"lcsc_{lc}.json").exists()
            errs: list[str] = []
            data = fetch_lcsc(lc, errs.append)
            if errs:
                rep.fetch_failed.append(f"{name} ({lc})")
            values.update(profiles.extract(group, data.get("params", {})))
            if not cached and delay:
                time.sleep(delay)
        elif not lc:
            rep.no_lcsc.append(name)
        for k, v in profiles.from_description(group, fields.get("Description", "")).items():
            values.setdefault(k, v)
        values = {k: v for k, v in values.items() if k in need}
        if tol_fix and "Tolerance" not in values:              # "1%" -> "±1%", same form as LCSC
            values["Tolerance"] = "±" + fields["Tolerance"].strip()
        # values holds only empty fields (or all with --overwrite) plus the ± fix,
        # so writing with overwrite=True never replaces a value you set.
        new_block, changed = set_fields(block, values, overwrite=True)
        if changed:
            rep.updated.append(name)
            rep.changes[name] = changed
            for k in changed:
                rep.filled[k] = rep.filled.get(k, 0) + 1
            out.append(text[pos:a])
            out.append(new_block)
            pos = b
        have = {**fields, **{k: v[1] for k, v in changed.items()}}
        for s in specs:
            if not (have.get(s.field) or "").strip():
                rep.still_missing.setdefault(s.field, []).append(name)
        if i % 20 == 0:
            log(f"  {i}/{len(blocks)} …")
    out.append(text[pos:])
    if rep.updated and not dry_run:
        tmp = SYM_LIB.with_suffix(".tmp")
        tmp.write_text("".join(out), encoding="utf-8")
        os.replace(tmp, SYM_LIB)
    return rep