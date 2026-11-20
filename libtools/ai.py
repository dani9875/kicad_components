"""Claude Code integration: Claude edits the library files directly.

Scope (enforced by Claude Code permission rules):
    edit:  pepy_sym_lib.kicad_sym, pepy_sym_lib.pretty/**, original_components/**
    read:  only inside the repository
    tools: Read, Edit, Write, Grep, Glob, WebFetch (no shell)
Anything else is refused: print mode cannot ask for approval.

Flow:
    snapshot()  before a run: library text, footprint files, file stats
    command()   headless `claude -p` call in the repo folder
    check()     after the run: what changed (symbols, pins, fills, fields, files)
    restore()   Discard / Undo: put the snapshot back
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .add import symbol_blocks
from .library import FP_DIR, LIB_NAME, ORIG_DIR, ROOT, SYM_LIB
from .sexp import dump, kid, kids, parse, walk

WORK_DIR = ROOT / ".ai_work"
TOOLS = "Read,Edit,Write,Grep,Glob,WebFetch"
EDIT_SCOPE = [f"./{SYM_LIB.name}", f"./{FP_DIR.name}/**", f"./{ORIG_DIR.name}/**"]

PIN_TYPES = ("input", "output", "bidirectional", "tri_state", "passive", "free", "unspecified",
             "power_in", "power_out", "open_collector", "open_emitter", "no_connect")

RULES = f"""\
You work in a KiCad component library. You may change ONLY:
- {SYM_LIB.name}        (all symbols, KiCad 10 format, tab indentation)
- {FP_DIR.name}/        (footprints, one .kicad_mod per footprint)
- {ORIG_DIR.name}/      (original EasyEDA files and 3D models per part)
Edits anywhere else are refused. There is no shell.

The symbol library is large. Use Grep to find the symbol, then Read with offset/limit.
Edit needs an old_string that is unique in the file: include the symbol name or pin
number lines around the text you change.

Rules:
- Change only what the user asks for, and only the part named below unless told otherwise.
- Keep symbol names, unit names (<name>_<unit>_<style>) and pin numbers.
- Do not move pins or change pin lengths unless asked.
- Keep KiCad 10 syntax and the existing indentation.
- Pin electrical types: input, output, bidirectional, tri_state, passive, free, unspecified,
  power_in, power_out, open_collector, open_emitter, no_connect.
- Fill types: none, outline, background, color.
- When a pin function is not clear, read the datasheet (WebFetch) before you decide.
- When you finish, reply with a short list of what you changed, one line per change.

Pin type guide:
- VCC, VDD, VIN, GND, VSS, exposed pad -> power_in.
- Regulator/converter output that supplies a net (VOUT) -> power_out. Switch node (SW, LX) -> power_out.
- Bootstrap, feedback, compensation, soft-start, current/timing set, analog -> passive.
- Enable / logic inputs -> input. Logic outputs -> output.
- Open-drain outputs (PGOOD, INT, ALERT, FAULT) -> open_collector.
- GPIO, SDA, data bus -> bidirectional. Pins marked NC -> no_connect.
- Two-terminal passives (R, C, L, ferrite, fuse) -> passive.
"""


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------
def claude_path() -> str | None:
    return shutil.which("claude")


# --------------------------------------------------------------------------
# Session (one conversation per part, resumable)
# --------------------------------------------------------------------------
def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")[:80]


@dataclass
class Session:
    part: str
    folder: Path            # .ai_work/<part>/ : session.json, CONTEXT.md (Claude cannot edit it)
    session_id: str = ""

    def save(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "session.json").write_text(
            json.dumps({"part": self.part, "session_id": self.session_id}, indent=1), encoding="utf-8")


def pin_table(node) -> str:
    rows = []
    for p in (n for n in walk(node) if n[0] == "pin"):
        rows.append(f"{(kid(p, 'number') or ['', ''])[1]:>5}  {(kid(p, 'name') or ['', ''])[1]:<20} {p[1]}")
    return "\n".join(rows)


def prepare(name: str, fields: dict, fresh: bool = False) -> Session:
    folder = WORK_DIR / slug(name)
    if fresh and folder.exists():
        shutil.rmtree(folder)
    s = Session(part=name, folder=folder)
    meta = folder / "session.json"
    if meta.exists():
        try:
            s.session_id = json.loads(meta.read_text(encoding="utf-8")).get("session_id", "")
        except json.JSONDecodeError:
            pass

    lib_text = SYM_LIB.read_text(encoding="utf-8")
    where, pins = "not found", ""
    for n, a, b in symbol_blocks(lib_text):
        if n == name:
            first = lib_text.count("\n", 0, a) + 1
            where = f"lines {first}-{first + lib_text.count(chr(10), a, b) - 1}"
            pins = pin_table(parse(lib_text[a:b])[0])
            break
    fp = fields.get("Footprint", "")
    fp_name = fp.split(":", 1)[1] if fp.startswith(LIB_NAME + ":") else ""
    info = "\n".join(f"- {k}: {v}" for k, v in fields.items() if v and not k.startswith("ki_"))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "CONTEXT.md").write_text(
        f"{RULES}\nSelected part: {name}\n"
        f"- Symbol: {SYM_LIB.name}, {where} (line numbers change when the file is edited)\n"
        f"- Footprint file: {FP_DIR.name}/{fp_name}.kicad_mod\n"
        f"- Original files: {ORIG_DIR.name}/{fp_name if fp_name and fp_name != name else name}/\n"
        f"{info}\n\nPins (number, name, type):\n{pins}\n", encoding="utf-8")
    s.save()
    return s


def command(s: Session, prompt: str, model: str = "") -> list[str]:
    """Run with cwd=ROOT.
    --tools            only these tools exist (no Bash)
    --allowedTools     pre-approves WebFetch and edits in the three library paths only
    blockReads...      refuses reads outside the repository
    Everything else needs approval, which print mode cannot give, so it is refused."""
    allow = ["WebFetch"] + [f"Edit({p})" for p in EDIT_SCOPE]
    cmd = [claude_path() or "claude", "-p", prompt,
           "--output-format", "stream-json", "--verbose",
           "--tools", TOOLS, "--allowedTools", *allow,
           "--permission-mode", "default",
           "--settings", json.dumps({"permissions": {"blockReadsOutsideWorkingDirectories": True}}),
           "--append-system-prompt-file", str(s.folder / "CONTEXT.md"),
           "--max-turns", "60"]
    if s.session_id:
        cmd += ["--resume", s.session_id]
    if model:
        cmd += ["--model", model]
    return cmd


# --------------------------------------------------------------------------
# Snapshot / review / restore
# --------------------------------------------------------------------------
def _stat_tree(root: Path) -> dict[str, tuple[int, int]]:
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            try:
                st = p.stat()
            except OSError:
                continue
            out[str(p.relative_to(ROOT))] = (st.st_size, st.st_mtime_ns)
    return out


@dataclass
class Snapshot:
    lib: str
    footprints: dict[str, bytes]          # relpath -> content (small, kept in memory)
    stats: dict[str, tuple[int, int]]     # relpath -> (size, mtime) for pretty + originals


def snapshot() -> Snapshot:
    fps = {str(p.relative_to(ROOT)): p.read_bytes() for p in FP_DIR.glob("*.kicad_mod")}
    stats = _stat_tree(FP_DIR)
    stats.update(_stat_tree(ORIG_DIR))
    return Snapshot(SYM_LIB.read_text(encoding="utf-8"), fps, stats)


@dataclass
class Review:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)       # changed/added/removed symbol names
    added: list[str] = field(default_factory=list)         # new files (relpath)
    changed: list[str] = field(default_factory=list)       # modified files (relpath)
    removed: list[str] = field(default_factory=list)       # deleted files (relpath)

    @property
    def empty(self) -> bool:
        return not (self.symbols or self.added or self.changed or self.removed)


def _pins(node) -> dict:
    out = {}
    for p in (n for n in walk(node) if n[0] == "pin"):
        at = kid(p, "at")
        out.setdefault((kid(p, "number") or ["", ""])[1], []).append(
            {"type": p[1], "name": (kid(p, "name") or ["", ""])[1],
             "at": tuple(at[1:4]) if at else (), "len": (kid(p, "length") or ["", ""])[1]})
    return out


def _symbol_diff(name: str, old, new, r: Review) -> None:
    for u in kids(new, "symbol"):
        if not u[1].startswith(name + "_"):
            r.errors.append(f"{name}: unit {u[1]} does not match the symbol name")
    po, pn = _pins(old), _pins(new)
    if set(po) != set(pn):
        r.changes.append(f"{name}: pin numbers changed (missing {sorted(set(po) - set(pn))[:8]}, "
                         f"new {sorted(set(pn) - set(po))[:8]})")
    bad = sorted({p[1] for p in (n for n in walk(new) if n[0] == "pin")} - set(PIN_TYPES))
    if bad:
        r.errors.append(f"{name}: invalid pin types {bad}")
    moved = 0
    for num in sorted(set(po) & set(pn), key=lambda x: (len(x), x)):
        for a, b in zip(po[num], pn[num]):
            if a["type"] != b["type"]:
                r.changes.append(f"pin {num} {b['name']}: {a['type']} → {b['type']}")
            if a["name"] != b["name"]:
                r.changes.append(f"pin {num} name: {a['name']} → {b['name']}")
            if a["at"] != b["at"] or a["len"] != b["len"]:
                moved += 1
    if moved:
        r.changes.append(f"{moved} pin(s) moved or resized")
    fo = [(kid(n, "type") or ["", ""])[1] for n in walk(old) if n[0] == "fill"]
    fn = [(kid(n, "type") or ["", ""])[1] for n in walk(new) if n[0] == "fill"]
    if fo != fn:
        r.changes.append(f"fill: {dict(Counter(fo))} → {dict(Counter(fn))}")
    a = {p[1]: p[2] for p in kids(old, "property")}
    b = {p[1]: p[2] for p in kids(new, "property")}
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            r.changes.append(f"field {k}: {a.get(k, '—')!r} → {b.get(k, '—')!r}")


def check(snap: Snapshot) -> Review:
    r = Review()
    # library
    new_text = SYM_LIB.read_text(encoding="utf-8")
    if new_text != snap.lib:
        try:
            new_blocks = {n: new_text[a:b] for n, a, b in symbol_blocks(new_text)}
            parse(new_text)
        except Exception as e:  # noqa: BLE001
            r.errors.append(f"{SYM_LIB.name} does not parse: {e}")
            new_blocks = {}
        old_blocks = {n: snap.lib[a:b] for n, a, b in symbol_blocks(snap.lib)}
        names = [n for n in sorted(set(old_blocks) | set(new_blocks)) if old_blocks.get(n) != new_blocks.get(n)]
        r.symbols = names
        for n in names:
            o, nw = old_blocks.get(n), new_blocks.get(n)
            if len(names) > 1:
                r.changes.append(f"[{n}]")
            if o is None:
                r.changes.append(f"symbol added: {n}")
            elif nw is None:
                r.changes.append(f"symbol removed: {n}")
            else:
                head = len(r.changes)
                _symbol_diff(n, parse(o)[0], parse(nw)[0], r)
                if len(r.changes) == head:
                    r.changes.append("graphics or formatting changed")
    # files
    now = _stat_tree(FP_DIR)
    now.update(_stat_tree(ORIG_DIR))
    for p in sorted(set(now) - set(snap.stats)):
        r.added.append(p)
        r.changes.append(f"new file: {p}")
    for p in sorted(set(snap.stats) - set(now)):
        r.removed.append(p)
        r.changes.append(f"deleted file: {p}")
    for p in sorted(set(now) & set(snap.stats)):
        if now[p] != snap.stats[p]:
            if p in snap.footprints and (ROOT / p).read_bytes() == snap.footprints[p]:
                continue
            r.changed.append(p)
            r.changes.append(f"changed file: {p}")
    for p in r.added + r.changed:
        if p.endswith(".kicad_mod"):
            try:
                parse((ROOT / p).read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                r.errors.append(f"{p} does not parse: {e}")
    r.ok = not r.errors
    return r


def restore(snap: Snapshot, r: Review) -> list[str]:
    """Put the snapshot back for everything this review lists. Returns notes."""
    notes = []
    if r.symbols:
        _write_lib(snap.lib if not _lib_changed_elsewhere(snap, r) else _restore_blocks(snap, r))
    for p in r.added:
        try:
            (ROOT / p).unlink()
        except FileNotFoundError:
            pass
    for p in r.changed + r.removed:
        if p in snap.footprints:
            (ROOT / p).write_bytes(snap.footprints[p])
        else:
            res = subprocess.run(["git", "restore", "--", p], cwd=ROOT, capture_output=True, text=True)
            notes.append(f"{p}: restored from git" if res.returncode == 0
                         else f"{p}: could not restore ({res.stderr.strip()})")
    return notes


def _lib_changed_elsewhere(snap: Snapshot, r: Review) -> bool:
    """True if symbols other than the reviewed ones differ from the snapshot (e.g. KiCad saved)."""
    cur = SYM_LIB.read_text(encoding="utf-8")
    a = {n: cur[x:y] for n, x, y in symbol_blocks(cur)}
    b = {n: snap.lib[x:y] for n, x, y in symbol_blocks(snap.lib)}
    return any(a.get(n) != b.get(n) for n in set(a) | set(b) if n not in r.symbols)


def _restore_blocks(snap: Snapshot, r: Review) -> str:
    """Restore only the reviewed symbols, keep everything else as it is now."""
    from .add import insert_symbol
    cur = SYM_LIB.read_text(encoding="utf-8")
    old = {n: snap.lib[x:y] for n, x, y in symbol_blocks(snap.lib)}
    for n in r.symbols:
        blocks = {m: (x, y) for m, x, y in symbol_blocks(cur)}
        if n in blocks:
            x, y = blocks[n]
            cur = cur[:x] + old.get(n, "") + cur[y:]
        elif n in old:
            cur = insert_symbol(cur, old[n].rstrip("\n"), n, overwrite=False)
    return cur


def _write_lib(text: str) -> None:
    tmp = SYM_LIB.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, SYM_LIB)


def normalise(names: list[str]) -> None:
    """Re-format changed symbols the way KiCad writes them (tabs, line wraps)."""
    text = SYM_LIB.read_text(encoding="utf-8")
    out, changed = text, False
    for n in names:
        for m, x, y in symbol_blocks(out):
            if m == n:
                new = dump(parse(out[x:y])[0], 1) + "\n"
                if new != out[x:y]:
                    out, changed = out[:x] + new + out[y:], True
                break
    if changed:
        _write_lib(out)