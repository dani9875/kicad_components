#!/usr/bin/env python3
"""pepy_sym_lib browser: search, filter, preview, add parts.

Search syntax (all terms must match, case-insensitive):
    10k                 text anywhere in the row
    tol:1%              field contains text   (field = column name prefix or alias)
    nvalue>=10k         numeric compare on engineering values (> < >= <= =)
    missing:tolerance   field is empty         has:dielectric   field is filled
    is:error  is:warn   rows with lint findings
    -murata             exclude rows containing text
    "two words"         quoted phrase
Aliases: ref val nv tol mfr lcsc desc fp pkg cat kw volt
"""
from __future__ import annotations

import json
import math
import re
import shlex
import sys
import webbrowser
from pathlib import Path

from PyQt6.QtCore import (QAbstractTableModel, QFileSystemWatcher, QModelIndex, QPointF, QProcess, QRectF,
                          QSettings, QSortFilterProxyModel, Qt, QThread, QTimer, QUrl, pyqtSignal)
from PyQt6.QtGui import (QAction, QBrush, QColor, QDesktopServices, QFont, QGuiApplication,
                         QKeySequence, QPainter, QPalette, QPen, QPolygonF)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                             QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QMainWindow, QMenu,
                             QPlainTextEdit, QPushButton, QSplitter, QTableView,
                             QTabWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from libtools import ai, lint
from libtools.inventory import Inventory
from libtools.add import AddError, add_part
from libtools import profiles
from libtools.library import group_of
from libtools.library import (FP_DIR, LIB_NAME, ORIG_DIR, SYM_LIB, Part, eng_to_float,
                              footprint_primitives, load_footprint, load_parts, symbol_primitives)

# --------------------------------------------------------------------------
# Columns
# --------------------------------------------------------------------------
STOCK = "Stock"
INV_NUM = "#"
GROUP = "Group"
SPECS = profiles.SPECS
COMMON_HEAD = [INV_NUM, "Part", GROUP, "Category", STOCK]
COMMON_TAIL = ["Reference", "Value", "Package", "Manufacturer", "LCSC Part", "Description",
               "Footprint", "Datasheet", "Keywords"]
PROFILE_FIELDS = profiles.all_fields()
LEAD_COLUMNS = COMMON_HEAD + [SPECS] + PROFILE_FIELDS + COMMON_TAIL   # every known column
FIELD_FOR_COLUMN = {"Keywords": "ki_keywords"}
ALIASES = {"ref": "Reference", "val": "Value", "nv": "Nvalue", "tol": "Tolerance",
           "mfr": "Manufacturer", "lcsc": "LCSC Part", "desc": "Description", "fp": "Footprint",
           "pkg": "Package", "cat": "Category", "kw": "Keywords", "volt": "Voltage rating",
           "stock": STOCK, "pcs": STOCK, "num": INV_NUM, "inv": INV_NUM, "group": GROUP, "grp": GROUP}
ENG_COLUMNS = profiles.numeric_fields() | {STOCK, INV_NUM}
ISSUES = "Issues"
ORIGINAL = "Original"


def original_files(p: Part):
    """(folder, files) in original_components for this part.
    Own folder for normal parts; for shared-footprint parts the raw symbol plus the shared files."""
    shared = bool(p.footprint) and p.footprint != p.name and (ORIG_DIR / p.footprint).is_dir()
    folder = ORIG_DIR / (p.footprint if shared else p.name)
    if not folder.is_dir():
        return folder, []
    files = sorted(f for f in folder.iterdir() if f.is_file())
    if shared:
        val = p.fields.get("Value", "")
        own = [f for f in files if f.suffix == ".kicad_sym" and (f.stem == p.name or (val and val in f.name))]
        common = [f for f in files if f.stem == folder.name]
        files = own + common
    return folder, files


def expected_columns(p: Part) -> set[str]:
    """Columns that should not be empty for this part (red when empty)."""
    if p.name.startswith(lint.VIRTUAL):
        return set()
    need = {"Description", "Manufacturer", "LCSC Part", "Datasheet", "Footprint"}
    prof = profiles.profile(group_of(p.category))
    if prof:
        need |= {s.field for s in prof} | {SPECS}
    return need


class PartsModel(QAbstractTableModel):
    SORT_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self):
        super().__init__()
        self.parts: list[Part] = []
        self.columns: list[str] = []
        self.issues: dict[str, list[lint.Finding]] = {}
        self.originals: dict = {}
        self.extra: list[str] = []
        self.group = ""
        self.inventory = Inventory()

    def stock(self, p: Part):
        if not self.inventory.loaded:
            return None
        return self.inventory.find(p.fields.get("LCSC Part", ""), p.fields.get("Value", ""))

    def load(self, parts, issues):
        self.beginResetModel()
        self.parts, self.issues = parts, issues
        extra = {}
        for p in parts:
            for k in p.fields:
                if k not in LEAD_COLUMNS and k not in FIELD_FOR_COLUMN.values():
                    extra[k] = extra.get(k, 0) + 1
        self.originals = {p.name: original_files(p) for p in parts}
        self.extra = sorted(extra, key=lambda k: -extra[k])
        self.columns = self.columns_for(self.group)
        self.endResetModel()

    def columns_for(self, group: str) -> list[str]:
        """Common columns + the group's own profile columns (or one Specs column)."""
        prof = [s.field for s in profiles.profile(group)]
        return COMMON_HEAD + (prof or [SPECS]) + COMMON_TAIL + self.extra + [ORIGINAL, ISSUES]

    def set_group(self, group: str):
        if group == self.group and self.columns:
            return
        self.beginResetModel()
        self.group = group
        self.columns = self.columns_for(group)
        self.endResetModel()

    def value(self, p: Part, col: str) -> str:
        if col == "Part":
            return p.name[len(p.category):] or p.name
        if col == "Category":
            return p.category.strip("[]")
        if col == GROUP:
            return group_of(p.category)
        if col == SPECS:
            return profiles.specs_text(group_of(p.category), p.fields)
        if col == INV_NUM:
            st = self.stock(p)
            return ", ".join(st.numbers) if st else ""
        if col == STOCK:
            st = self.stock(p)
            if not st:
                return ""
            return f"{st.pcs:g}" if st.counted else "?"
        if col == ORIGINAL:
            folder, files = self.originals.get(p.name, (None, []))
            return " ".join(sorted({f.suffix.lstrip(".").replace("kicad_", "") for f in files}))
        if col == ISSUES:
            f = self.issues.get(p.name, [])
            e = sum(1 for x in f if x.level == lint.ERR)
            return f"{e} err  {len(f) - e} warn" if f else ""
        return p.fields.get(FIELD_FOR_COLUMN.get(col, col), "")

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.parts)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, s, o, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and o == Qt.Orientation.Horizontal:
            return self.columns[s]
        return None

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        p, col = self.parts[idx.row()], self.columns[idx.column()]
        v = self.value(p, col)
        if role == Qt.ItemDataRole.DisplayRole:
            return v
        if role == Qt.ItemDataRole.ToolTipRole:
            if col in (STOCK, INV_NUM) and self.inventory.loaded:
                st = self.stock(p)
                if not st:
                    return "Not in the inventory"
                where = ", ".join(f"row {r}" for r in st.rows)
                nums = f" · #{', #'.join(st.numbers)}" if st.numbers else " · no # yet"
                return ("Not counted yet" if not st.counted else f"{st.pcs:g} pcs") + f" ({where}{nums})"
            if col == ISSUES:
                return "\n".join(f"{f.level}: {f.rule}" + (f" ({f.detail})" if f.detail else "")
                                 for f in self.issues.get(p.name, []))
            return v if len(v) > 30 else None
        if role == Qt.ItemDataRole.BackgroundRole:
            if not v and (col in expected_columns(p) or (col == ORIGINAL and not p.name.startswith(lint.VIRTUAL))):
                return QBrush(QColor(110, 40, 40))
            if self.inventory.loaded and not p.name.startswith(lint.VIRTUAL):
                if col == INV_NUM and not v:
                    return QBrush(QColor(110, 40, 40))       # no inventory number = not in the inventory
                if col == STOCK and v in ("0", "?"):
                    return QBrush(QColor(95, 80, 30))        # not counted / none left
            if col == ISSUES and v:
                err = any(f.level == lint.ERR for f in self.issues.get(p.name, []))
                return QBrush(QColor(130, 35, 35) if err else QColor(95, 80, 30))
        if role == Qt.ItemDataRole.ForegroundRole and col == "Datasheet" and v:
            return QBrush(QColor(110, 170, 255))
        if role == self.SORT_ROLE:
            if col in ENG_COLUMNS:
                f = eng_to_float(v.lstrip("±"))
                return f if f is not None else math.inf
            if col == ISSUES:
                f = self.issues.get(p.name, [])
                return sum(10 if x.level == lint.ERR else 1 for x in f)
            return v.lower()
        return None


# --------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------
class PartsFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.group = ""
        self.category = ""
        self.terms: list = []
        self.issues_only = False
        self.setSortRole(PartsModel.SORT_ROLE)

    def resolve(self, key: str) -> str | None:
        m: PartsModel = self.sourceModel()
        k = key.lower().replace("_", " ")
        if k in ALIASES:
            return ALIASES[k]
        for c in m.columns:
            if c.lower().startswith(k):
                return c
        return None

    def set_query(self, text: str):
        try:
            toks = shlex.split(text)
        except ValueError:
            toks = text.split()
        terms = []
        for t in toks:
            neg = t.startswith("-") and len(t) > 1
            t = t[1:] if neg else t
            m = re.match(r"^(missing|has):(.+)$", t, re.I)
            if m:
                terms.append(("presence", neg, m.group(1).lower() == "has", self.resolve(m.group(2))))
                continue
            m = re.match(r"^is:(error|err|warn|warning|ok)$", t, re.I)
            if m:
                terms.append(("is", neg, m.group(1).lower()[:3]))
                continue
            m = re.match(r"^([\w ]+?)(>=|<=|>|<|=)(.+)$", t)
            if m and self.resolve(m.group(1)) and eng_to_float(m.group(3)) is not None:
                terms.append(("cmp", neg, self.resolve(m.group(1)), m.group(2), eng_to_float(m.group(3))))
                continue
            m = re.match(r"^([\w ]+?):(.*)$", t)
            if m and self.resolve(m.group(1)):
                terms.append(("field", neg, self.resolve(m.group(1)), m.group(2).lower()))
                continue
            terms.append(("text", neg, t.lower()))
        self.terms = terms
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        m: PartsModel = self.sourceModel()
        p = m.parts[row]
        if self.category and p.category != self.category:
            return False
        if self.group and group_of(p.category) != self.group:
            return False
        issues = m.issues.get(p.name, [])
        if self.issues_only and not issues:
            return False
        for t in self.terms:
            kind, neg = t[0], t[1]
            if kind == "text":
                ok = t[2] in p.name.lower() or any(t[2] in m.value(p, c).lower() for c in m.columns)
            elif kind == "field":
                ok = t[2] is not None and t[3] in m.value(p, t[2]).lower()
            elif kind == "presence":
                ok = t[3] is not None and bool(m.value(p, t[3]).strip()) == t[2]
            elif kind == "is":
                if t[2] == "err":
                    ok = any(f.level == lint.ERR for f in issues)
                elif t[2] == "war":
                    ok = any(f.level == lint.WARN for f in issues)
                else:
                    ok = not issues
            else:  # cmp
                v = eng_to_float(m.value(p, t[2]))
                op, ref = t[3], t[4]
                ok = v is not None and {">": v > ref, "<": v < ref, ">=": v >= ref * (1 - 1e-9),
                                        "<=": v <= ref * (1 + 1e-9), "=": math.isclose(v, ref, rel_tol=1e-6)}[op]
            if ok == neg:
                return False
        return True


# --------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------
COLORS = {
    "body": QColor(210, 95, 95), "bodyfill": QColor(78, 68, 34), "pin": QColor(210, 95, 95),
    "pinname": QColor(120, 205, 205), "pinnum": QColor(185, 180, 120), "text": QColor(210, 210, 210),
    "pad": QColor(200, 52, 52, 210), "pad_b": QColor(77, 127, 196, 210), "npth": QColor(120, 120, 120),
    "silk": QColor(242, 237, 161), "fab": QColor(175, 175, 175), "crtyd": QColor(255, 38, 226),
    "edge": QColor(208, 210, 50), "bsilk": QColor(232, 178, 167), "bfab": QColor(88, 93, 132),
    "bcrtyd": QColor(38, 233, 255),
}
FP_LEGEND = [("pad", "Pads"), ("silk", "Silk"), ("fab", "Fab"), ("crtyd", "Courtyard")]


class Preview(QWidget):
    def __init__(self, title: str):
        super().__init__()
        self.title = title
        self.prims: list = []
        self.flip = False
        self.note = ""
        self.setMinimumHeight(180)

    def show_prims(self, prims, flip: bool, note: str = ""):
        self.prims, self.flip, self.note = prims, flip, note
        self.update()

    def _bbox(self):
        xs, ys = [], []
        for p in self.prims:
            t = p[0]
            if t in ("line", "poly"):
                for x, y in p[1]:
                    xs.append(x), ys.append(y)
            elif t == "circle":
                (cx, cy), r = p[1], p[2]
                xs += [cx - r, cx + r]
                ys += [cy - r, cy + r]
            elif t == "pad":
                (cx, cy), (w, h) = p[2], p[3]
                rr = math.hypot(w, h) / 2 if p[4] % 90 else max(w, h) / 2
                xs += [cx - rr, cx + rr]
                ys += [cy - rr, cy + rr]
            elif t == "text":
                xs.append(p[1][0]), ys.append(p[1][1])
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def paintEvent(self, _):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.fillRect(self.rect(), QColor(24, 24, 28))
        qp.setPen(QColor(150, 150, 150))
        qp.setFont(QFont(self.font().family(), 9))
        qp.drawText(8, 16, self.title)
        if self.title == "Footprint" and self.prims:
            x = 80
            for k, label in FP_LEGEND:
                qp.fillRect(x, 8, 10, 10, COLORS[k])
                qp.setPen(QColor(150, 150, 150))
                qp.drawText(x + 14, 17, label)
                x += 24 + qp.fontMetrics().horizontalAdvance(label)
        bb = self._bbox()
        if not bb:
            qp.setPen(QColor(120, 120, 120))
            qp.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.note or "No preview")
            return
        x0, y0, x1, y1 = bb
        w, h = max(x1 - x0, 0.5), max(y1 - y0, 0.5)
        m = 28
        s = min((self.width() - 2 * m) / w, (self.height() - 2 * m - 14) / h, 80)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        ox, oy = self.width() / 2, self.height() / 2 + 7

        def mp(x, y):
            return QPointF(ox + (x - cx) * s, oy - (y - cy) * s if self.flip else oy + (y - cy) * s)

        texts = []
        order = {"crtyd": 0, "bcrtyd": 0, "fab": 1, "bfab": 1, "pad": 2, "pad_b": 2, "npth": 2,
                 "silk": 3, "bsilk": 3, "edge": 4}
        def key(p):
            k = {"pad": 5, "line": 2, "poly": 2, "circle": 3}.get(p[0])
            return order.get(p[k], 5) if k is not None else 9
        for p in sorted(self.prims, key=key):
            t = p[0]
            if t == "line":
                qp.setPen(QPen(COLORS.get(p[2], COLORS["body"]), 1.6))
                qp.drawPolyline(QPolygonF([mp(*pt) for pt in p[1]]))
            elif t == "poly":
                kind, filled = p[2], p[3]
                qp.setPen(QPen(COLORS.get(kind, COLORS["body"]), 1.6))
                if filled:
                    qp.setBrush(COLORS["bodyfill"] if kind == "body" else COLORS.get(kind))
                else:
                    qp.setBrush(Qt.BrushStyle.NoBrush)
                qp.drawPolygon(QPolygonF([mp(*pt) for pt in p[1]]))
            elif t == "circle":
                (x, y), r, kind, filled = p[1], p[2], p[3], p[4]
                qp.setPen(QPen(COLORS.get(kind, COLORS["body"]), 1.6))
                qp.setBrush(COLORS["bodyfill"] if filled and kind == "body" else Qt.BrushStyle.NoBrush)
                qp.drawEllipse(mp(x, y), r * s, r * s)
            elif t == "pad":
                shape, (x, y), (pw, ph), ang, kind, drill, number = p[1:]
                qp.save()
                qp.translate(mp(x, y))
                qp.rotate(-ang)
                qp.setPen(Qt.PenStyle.NoPen)
                qp.setBrush(COLORS[kind])
                r = QRectF(-pw * s / 2, -ph * s / 2, pw * s, ph * s)
                if shape == "circle":
                    qp.drawEllipse(r)
                elif shape == "oval":
                    rad = min(pw, ph) * s / 2
                    qp.drawRoundedRect(r, rad, rad)
                elif shape == "roundrect":
                    rad = min(pw, ph) * s * 0.25
                    qp.drawRoundedRect(r, rad, rad)
                else:
                    qp.drawRect(r)
                if drill > 0:
                    qp.setBrush(QColor(24, 24, 28))
                    qp.drawEllipse(QPointF(0, 0), drill * s / 2, drill * s / 2)
                qp.restore()
                if number and min(pw, ph) * s > 9:
                    texts.append((mp(x, y), number, min(pw, ph) * s * 0.55, QColor(255, 255, 255), 0))
            elif t == "text":
                (x, y), text, size, kind = p[1], p[2], p[3], p[4]
                ang = p[5] if len(p) > 5 else None
                texts.append((mp(x, y), text, size * s, COLORS.get(kind, COLORS["text"]), ang))

        for pt, text, px, color, ang in texts:
            px = max(7.0, min(px, 14.0))
            if px < 7.5 and len(self.prims) > 400:
                continue
            qp.setFont(QFont(self.font().family(), 1))
            f = qp.font()
            f.setPixelSize(int(px))
            qp.setFont(f)
            qp.setPen(color)
            fm = qp.fontMetrics()
            tw = fm.horizontalAdvance(text)
            if ang is None or ang == 0:
                dx = -tw / 2 if ang is None else 0
            else:
                a = ang % 360
                dx = 0 if a < 45 or a > 315 else (-tw if 135 < a < 225 else -tw / 2)
            qp.drawText(QPointF(pt.x() + dx, pt.y() + fm.ascent() / 2 - 1), text)


# --------------------------------------------------------------------------
# Add dialog
# --------------------------------------------------------------------------
class AddWorker(QThread):
    line = pyqtSignal(str)
    done = pyqtSignal(object, str)

    def __init__(self, lcsc, name, overwrite, dry):
        super().__init__()
        self.args = (lcsc, name, overwrite, dry)

    def run(self):
        lcsc, name, overwrite, dry = self.args
        try:
            r = add_part(lcsc, name, overwrite=overwrite, dry_run=dry, log=self.line.emit)
            self.done.emit(r, "")
        except AddError as e:
            self.done.emit(None, str(e))
        except Exception as e:  # noqa: BLE001
            self.done.emit(None, f"{e.__class__.__name__}: {e}")


class AddDialog(QDialog):
    added = pyqtSignal(str)

    def __init__(self, parent, categories: list[str], category: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Add part from LCSC")
        self.resize(640, 420)
        self.lcsc = QLineEdit()
        self.lcsc.setPlaceholderText("C17414")
        self.cat = QComboBox()
        self.cat.setEditable(True)
        self.cat.addItems([""] + categories)
        self.cat.setCurrentText(category)
        self.part = QLineEdit()
        self.part.setPlaceholderText("Empty = Manufacturer MPN from LCSC")
        self.overwrite = QCheckBox("Overwrite if it exists")
        self.dry = QCheckBox("Dry run (write nothing)")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("monospace", 9))
        self.go = QPushButton("Add")
        self.go.setDefault(True)
        self.go.clicked.connect(self.start)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)

        form = QFormLayout()
        form.addRow("LCSC number", self.lcsc)
        form.addRow("Category", self.cat)
        form.addRow("Part name", self.part)
        opts = QHBoxLayout()
        opts.addWidget(self.overwrite)
        opts.addWidget(self.dry)
        opts.addStretch()
        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(self.go)
        btns.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(opts)
        lay.addWidget(self.log, 1)
        lay.addLayout(btns)
        self.worker = None

    def name(self) -> str:
        cat = self.cat.currentText().strip()
        if cat and not cat.startswith("["):
            cat = f"[{cat}]"
        return cat + self.part.text().strip()

    def start(self):
        if not self.lcsc.text().strip() or not self.cat.currentText().strip():
            self.log.appendPlainText("Enter the LCSC number and the category.")
            return
        self.go.setEnabled(False)
        self.log.appendPlainText(f"--- {self.lcsc.text().strip()} -> {self.name()}")
        self.worker = AddWorker(self.lcsc.text(), self.name(), self.overwrite.isChecked(), self.dry.isChecked())
        self.worker.line.connect(self.log.appendPlainText)
        self.worker.done.connect(self.finished_add)
        self.worker.start()

    def finished_add(self, r, err):
        self.go.setEnabled(True)
        if err:
            self.log.appendPlainText(f"Error: {err}")
            return
        self.log.appendPlainText(f"name:      {r.name}\nsymbol:    {r.symbol_mode}\nfootprint: {r.footprint}\n3D model:  {r.model}")
        if r.warnings:
            self.log.appendPlainText("Check:\n" + "\n".join(f"  - {w}" for w in r.warnings))
        if self.dry.isChecked():
            self.log.appendPlainText("\n" + r.symbol_text)
        else:
            self.added.emit(r.name)



# --------------------------------------------------------------------------
# Claude Code panel
# --------------------------------------------------------------------------
QUICK_PROMPTS = {
    "Fill → none": "Set every fill in this symbol to (fill (type none)). Change nothing else.",
    "Set pin types": ("Set the electrical type of every pin correctly for this part. "
                      "Check the datasheet when a pin function is not clear. Change nothing else."),
    "Both": ("1) Set every fill in this symbol to (fill (type none)). "
             "2) Set the electrical type of every pin correctly for this part; check the datasheet "
             "when a pin function is not clear. Change nothing else."),
}


def open_terminal(cmd: list[str]) -> bool:
    """Run cmd in a new terminal window. Returns False if no terminal was found."""
    import shutil
    if sys.platform == "darwin":
        script = f'tell application "Terminal" to do script "{" ".join(cmd)}"'
        return QProcess.startDetached("osascript", ["-e", script])[0]
    if sys.platform.startswith("win"):
        return QProcess.startDetached("cmd", ["/c", "start", "cmd", "/k", *cmd])[0]
    for term, args in (("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("konsole", ["-e"]),
                       ("xfce4-terminal", ["-x"]), ("kitty", []), ("alacritty", ["-e"]),
                       ("wezterm", ["start", "--"]), ("xterm", ["-e"])):
        if shutil.which(term):
            return QProcess.startDetached(term, args + cmd)[0]
    return False


class ClaudePanel(QWidget):
    library_changed = pyqtSignal(str)   # part name

    def __init__(self, settings: QSettings):
        super().__init__()
        self.settings = settings
        self.part: Part | None = None
        self.session: ai.Session | None = None
        self.review: ai.Review | None = None
        self.snap: ai.Snapshot | None = None        # state before the current run
        self.undo_data: tuple | None = None          # (snapshot, review) of the last kept run
        self.proc: QProcess | None = None
        self.buf = b""

        self.status = QLabel("Checking Claude Code …")
        self.login_btn = QPushButton("Log in")
        self.login_btn.clicked.connect(self.login)
        self.model = QComboBox()
        self.model.addItems(["default model", "sonnet", "opus", "haiku"])
        self.model.setCurrentText(settings.value("ai_model", "default model") or "default model")
        self.model.currentTextChanged.connect(lambda t: settings.setValue("ai_model", t))
        self.mode = QComboBox()
        self.mode.addItems(["Docker", "Local"])
        self.mode.setToolTip("Docker: Claude runs in a container that sees only the library files.\n"
                             "Local: Claude runs on your PC, limited by permission rules.")
        self.mode.setCurrentText(settings.value("ai_mode", "Docker") or "Docker")
        self.mode.currentTextChanged.connect(self._mode_changed)
        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.mode)
        top.addWidget(self.model)
        top.addWidget(self.login_btn)

        self.part_lbl = QLabel("No part selected")
        self.part_lbl.setWordWrap(True)
        self.new_btn = QPushButton("New conversation")
        self.new_btn.clicked.connect(self.new_conversation)
        row = QHBoxLayout()
        row.addWidget(self.part_lbl, 1)
        row.addWidget(self.new_btn)

        quick = QHBoxLayout()
        self.quick_btns = []
        for label, prompt in QUICK_PROMPTS.items():
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, p=prompt: self.send(p))
            quick.addWidget(b)
            self.quick_btns.append(b)

        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("Ask Claude to change this part …   (Ctrl+Enter to send)")
        self.prompt.setFixedHeight(64)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(lambda: self.send(self.prompt.toPlainText()))
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.setEnabled(False)
        a = QAction(self.prompt)
        a.setShortcut(QKeySequence("Ctrl+Return"))
        a.triggered.connect(lambda: self.send(self.prompt.toPlainText()))
        self.prompt.addAction(a)
        prow = QHBoxLayout()
        prow.addWidget(self.prompt, 1)
        col = QVBoxLayout()
        col.addWidget(self.send_btn)
        col.addWidget(self.stop_btn)
        prow.addLayout(col)

        self.log = QTextBrowser()
        self.log.setOpenExternalLinks(True)

        self.changes = QTextBrowser()
        self.changes.setMaximumHeight(150)
        self.apply_btn = QPushButton("Keep")
        self.apply_btn.clicked.connect(self.apply)
        self.discard_btn = QPushButton("Discard")
        self.discard_btn.clicked.connect(self.discard)
        self.undo_btn = QPushButton("Undo last kept")
        self.undo_btn.clicked.connect(self.undo)
        rrow = QHBoxLayout()
        rrow.addWidget(self.apply_btn)
        rrow.addWidget(self.discard_btn)
        rrow.addStretch()
        rrow.addWidget(self.undo_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(top)
        lay.addLayout(row)
        lay.addLayout(quick)
        lay.addLayout(prow)
        lay.addWidget(self.log, 1)
        lay.addWidget(QLabel("Changes made by Claude (already in the files — Keep or Discard)"))
        lay.addWidget(self.changes)
        lay.addLayout(rrow)

        self.logged_in = False
        self.auth_proc: QProcess | None = None
        self.poll = QTimer(interval=3000)
        self.poll.timeout.connect(self.check_auth)
        self.poll_left = 0
        self.check_auth()
        self.update_buttons()

    # ---------------------------------------------------------------- auth
    def docker(self) -> bool:
        return self.mode.currentText() == "Docker"

    def _mode_changed(self, t):
        self.settings.setValue("ai_mode", t)
        self.logged_in = False
        self.check_auth()

    def check_auth(self):
        if self.docker():
            ok, msg = ai.docker_ready()
            if not ok:
                self.status.setText(msg)
                self.status.setToolTip(msg)
                self.logged_in = False
                self.update_buttons()
                return
        elif not ai.claude_path():
            self.status.setText("Claude Code not installed: npm install -g @anthropic-ai/claude-code")
            self.logged_in = False
            self.update_buttons()
            return
        if self.auth_proc and self.auth_proc.state() != QProcess.ProcessState.NotRunning:
            return
        cmd = ai.auth_status_cmd(self.docker())
        self.auth_proc = QProcess(self)
        self.auth_proc.finished.connect(self._auth_done)
        self.auth_proc.start(cmd[0], cmd[1:])

    def _auth_done(self, code, _status):
        out = bytes(self.auth_proc.readAllStandardOutput()).decode(errors="replace")
        try:
            d = json.loads(out)
            ok = bool(d.get("loggedIn"))
            who = d.get("email") or d.get("authMethod", "")
        except json.JSONDecodeError:
            ok, who = code == 0, ""
        self.logged_in = ok
        where = "Docker" if self.docker() else "local"
        self.status.setText(f"Claude Code ({where}): logged in {('· ' + who) if who else ''}" if ok
                            else f"Claude Code ({where}): not logged in")
        self.login_btn.setText("Log out" if ok else "Log in")
        if ok or self.poll_left <= 0:
            self.poll.stop()
        self.poll_left -= 1
        self.update_buttons()

    def login(self):
        docker = self.docker()
        if self.logged_in:
            cmd = ai.logout_cmd(docker)
            QProcess.execute(cmd[0], cmd[1:])
            self.check_auth()
            return
        cmd = ai.login_cmd(docker)
        if not open_terminal(cmd):
            self.log.append("No terminal found. Run this in a terminal, then press Log in again:<br>"
                            f"<code>{_esc(' '.join(cmd))}</code>")
            return
        self.log.append("Log in in the terminal window. This panel updates when you are done."
                        + (" In Docker, open the printed URL in your browser and paste the code back "
                           "into the terminal." if docker else ""))
        self.poll_left = 60
        self.poll.start()

    # ---------------------------------------------------------------- part
    def set_part(self, p: Part | None):
        if self.running() or self.pending():
            return  # finish the current run first (Keep / Discard)
        self.part = p
        if not self.session or not p or self.session.part != p.name:
            self.session = None
            self.log.clear()
            self.changes.clear()
        self.part_lbl.setText(f"<b>{_esc(p.name)}</b>" if p else "No part selected")
        self.update_buttons()

    def new_conversation(self):
        if not self.part or self.running() or self.pending():
            return
        self.session = ai.prepare(self.part.name, self.part.fields, fresh=True)
        self.log.clear()
        self.changes.clear()
        self.log.append("<i>New conversation.</i>")
        self.update_buttons()

    # ---------------------------------------------------------------- run
    def running(self) -> bool:
        return bool(self.proc and self.proc.state() != QProcess.ProcessState.NotRunning)

    def pending(self) -> bool:
        return bool(self.review and not self.review.empty)

    def update_buttons(self):
        can = self.logged_in and self.part is not None and not self.running() and not self.pending()
        for b in self.quick_btns + [self.send_btn]:
            b.setEnabled(can)
        self.stop_btn.setEnabled(self.running())
        self.new_btn.setEnabled(self.part is not None and not self.running() and not self.pending())
        self.apply_btn.setEnabled(self.pending() and not self.running())
        self.discard_btn.setEnabled(self.pending() and not self.running())
        self.undo_btn.setEnabled(self.undo_data is not None and not self.running() and not self.pending())

    def send(self, prompt: str):
        prompt = prompt.strip()
        if not prompt or not self.part or self.running() or self.pending():
            return
        self.session = ai.prepare(self.part.name, self.part.fields)
        self.snap = ai.snapshot()
        self.review = None
        self.changes.clear()
        self.prompt.clear()
        self.log.append(f"<p style='color:#8ab4f8'><b>You:</b> {_esc(prompt)}</p>"
                        f"<i style='color:#999'>{'in Docker' if self.docker() else 'local'}</i>")
        model = self.model.currentText()
        try:
            cmd = ai.command(self.session, prompt, "" if model == "default model" else model, docker=self.docker())
        except RuntimeError as e:
            self.log.append(f"<span style='color:#ff8a80'>Not started: {_esc(str(e))}</span>")
            self.snap = None
            self.update_buttons()
            return
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(str(ai.ROOT))
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.readyReadStandardError.connect(self._read_err)
        self.proc.finished.connect(self._done)
        self.buf = b""
        self.proc.start(cmd[0], cmd[1:])
        self.update_buttons()

    def stop(self):
        if self.running():
            self.proc.kill()
            self.log.append("<i>Stopped. Check the changes below.</i>")

    def _read_err(self):
        err = bytes(self.proc.readAllStandardError()).decode(errors="replace").strip()
        if err:
            self.log.append(f"<span style='color:#ff8a80'>{_esc(err)}</span>")

    def _read(self):
        self.buf += bytes(self.proc.readAllStandardOutput())
        *lines, self.buf = self.buf.split(b"\n")
        for line in lines:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._event(ev)

    def _event(self, ev: dict):
        t = ev.get("type")
        if ev.get("session_id") and self.session and self.session.session_id != ev["session_id"]:
            self.session.session_id = ev["session_id"]
            self.session.save()
        if t == "system" and ev.get("subtype") == "init":
            self.log.append(f"<i style='color:#999'>Claude Code · {_esc(ev.get('model', ''))}</i>")
        elif t == "assistant":
            for c in ev.get("message", {}).get("content", []):
                if c.get("type") == "text" and c.get("text", "").strip():
                    self.log.append(f"<p><b>Claude:</b> {_esc(c['text']).replace(chr(10), '<br>')}</p>")
                elif c.get("type") == "tool_use":
                    inp = c.get("input", {})
                    what = inp.get("url") or inp.get("pattern") or Path(inp.get("file_path", "")).name or ""
                    self.log.append(f"<span style='color:#999'>→ {_esc(c.get('name', ''))} {_esc(what)}</span>")
        elif t == "user":
            content = ev.get("message", {}).get("content")
            for c in content if isinstance(content, list) else []:
                if c.get("type") == "tool_result" and c.get("is_error"):
                    txt = c.get("content")
                    txt = txt if isinstance(txt, str) else json.dumps(txt)
                    self.log.append(f"<span style='color:#e6c35c'>  refused / error: {_esc(txt[:300])}</span>")
        elif t == "result":
            secs = ev.get("duration_ms", 0) / 1000
            cost = ev.get("total_cost_usd")
            tail = f" · ${cost:.3f}" if isinstance(cost, (int, float)) else ""
            if ev.get("is_error"):
                self.log.append(f"<span style='color:#ff8a80'>Error: {_esc(str(ev.get('result') or ev.get('subtype')))}</span>")
            self.log.append(f"<i style='color:#999'>Done in {secs:.0f} s{tail}</i>")

    def _done(self, code, _status):
        self._read()
        if code != 0:
            self.log.append(f"<span style='color:#ff8a80'>claude exited with code {code}.</span>")
        if self.snap:
            r = ai.check(self.snap)
            if r.symbols and r.ok:
                ai.normalise(r.symbols)
                r = ai.check(self.snap)
            self.review = r
            self.show_review()
        self.update_buttons()
        self.library_changed.emit(self.part.name if self.part else "")

    # ---------------------------------------------------------------- review
    def show_review(self):
        r = self.review
        html = "".join(f"<div style='color:#ff8a80'>✗ {_esc(e)}</div>" for e in r.errors)
        html += "".join(f"<div>{'<b>' if c.startswith('[') else '• '}{_esc(c)}{'</b>' if c.startswith('[') else ''}</div>"
                        for c in r.changes)
        if r.empty:
            html = "<i style='color:#999'>No files changed.</i>"
        elif r.errors:
            html = "<b style='color:#ff8a80'>Problems found — Discard is recommended.</b>" + html
        self.changes.setHtml(html)

    def apply(self):
        if not self.pending():
            return
        self.undo_data = (self.snap, self.review)
        self.log.append("<b style='color:#7c7'>Kept.</b>")
        self.review = None
        self.changes.clear()
        self.update_buttons()

    def discard(self):
        if not self.pending():
            return
        for n in ai.restore(self.snap, self.review):
            self.log.append(f"<span style='color:#999'>{_esc(n)}</span>")
        self.review = None
        self.changes.clear()
        self.log.append("<i>Discarded. Files are back as they were before this run. "
                        "Tell Claude in your next message if it should not repeat it.</i>")
        self.update_buttons()
        self.library_changed.emit(self.part.name if self.part else "")

    def undo(self):
        if not self.undo_data or self.running() or self.pending():
            return
        snap, review = self.undo_data
        for n in ai.restore(snap, review):
            self.log.append(f"<span style='color:#999'>{_esc(n)}</span>")
        self.undo_data = None
        self.log.append("<i>Undone.</i>")
        self.update_buttons()
        self.library_changed.emit(self.part.name if self.part else "")

def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------
class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{LIB_NAME} browser")
        self.settings = QSettings("pepy", "library_browser")
        self.model = PartsModel()
        self.proxy = PartsFilter()
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        # top bar
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search   e.g.  tol:1% nvalue>=10k  missing:tolerance  mfr:yageo  is:error')
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip(__doc__)
        self.search.textChanged.connect(lambda: self.search_timer.start(150))
        self.search_timer = QTimer(singleShot=True)
        self.search_timer.timeout.connect(lambda: (self.proxy.set_query(self.search.text()), self.update_status()))
        self.issues_only = QCheckBox("Only with issues")
        self.issues_only.toggled.connect(self.toggle_issues)
        cols = QToolButton(text="Columns")
        cols.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.cols_menu = QMenu(self)
        cols.setMenu(self.cols_menu)
        add_btn = QPushButton("Add part…")
        add_btn.clicked.connect(self.open_add)
        reload_btn = QPushButton("Reload")
        reload_btn.clicked.connect(self.reload)
        self.inv_btn = QPushButton("Inventory…")
        self.inv_btn.setToolTip("Choose Inventory.xlsx. The Stock column reads the PCS column of its Inventory sheet.")
        self.inv_btn.clicked.connect(self.choose_inventory)
        self.group_box = QComboBox()
        self.group_box.setMinimumWidth(190)
        self.group_box.currentIndexChanged.connect(self.group_changed)
        self.cat_box = QComboBox()
        self.cat_box.setMinimumWidth(240)
        self.cat_box.setMaxVisibleItems(30)
        self.cat_box.currentIndexChanged.connect(self.cat_changed)
        top = QHBoxLayout()
        top.addWidget(self.group_box)
        top.addWidget(self.cat_box)
        top.addWidget(self.search, 1)
        top.addWidget(self.issues_only)
        top.addWidget(cols)
        top.addWidget(self.inv_btn)
        top.addWidget(reload_btn)
        top.addWidget(add_btn)


        # table
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionsMovable(True)
        hh.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hh.customContextMenuRequested.connect(lambda pos: self.cols_menu.exec(hh.mapToGlobal(pos)))
        self.table.selectionModel().currentRowChanged.connect(self.row_changed)
        self.table.doubleClicked.connect(self.double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.row_menu)

        # detail
        self.sym_view = Preview("Symbol")
        self.fp_view = Preview("Footprint")
        self.info = QTextBrowser()
        self.info.setOpenLinks(False)
        self.info.anchorClicked.connect(lambda u: QDesktopServices.openUrl(u))
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.sym_view)
        right.addWidget(self.fp_view)
        self.claude = ClaudePanel(self.settings)
        self.claude.library_changed.connect(lambda name: self.reload(select=name))
        self.tabs = QTabWidget()
        self.tabs.addTab(self.info, "Details")
        self.tabs.addTab(self.claude, "Claude")
        right.addWidget(self.tabs)
        right.setSizes([260, 260, 300])

        self.split = QSplitter()
        self.split.addWidget(self.table)
        self.split.addWidget(right)
        self.split.setSizes([1200, 420])
        self.right = right

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addLayout(top)
        lay.addWidget(self.split, 1)
        self.setCentralWidget(central)
        self.status = QLabel()
        self.statusBar().addWidget(self.status)

        for seq, fn in ((QKeySequence.StandardKey.Find, lambda: (self.search.setFocus(), self.search.selectAll())),
                        (QKeySequence("Ctrl+N"), self.open_add), (QKeySequence("F5"), self.reload)):
            a = QAction(self)
            a.setShortcut(seq)
            a.triggered.connect(fn)
            self.addAction(a)

        # watch files
        self.watcher = QFileSystemWatcher([str(SYM_LIB), str(FP_DIR)])
        self.reload_timer = QTimer(singleShot=True)
        self.reload_timer.timeout.connect(self.reload)
        self.watcher.fileChanged.connect(lambda *_: self.reload_timer.start(600))
        self.watcher.directoryChanged.connect(lambda *_: self.reload_timer.start(600))

        self._hidden: set[str] = set()
        try:
            self.col_widths = json.loads(self.settings.value("col_widths", "{}") or "{}")
        except (TypeError, json.JSONDecodeError):
            self.col_widths = {}
        self.sort_col = (self.settings.value("sort_col", "Category") or "Category",
                         Qt.SortOrder(int(self.settings.value("sort_order", 0) or 0)))
        self.reload()
        self.restore()

    # ---------------------------------------------------------------- data
    def reload(self, select: str = ""):
        cur = select or self.current_name()
        parts = load_parts()
        findings = lint.run(parts)
        self.n_err = sum(1 for f in findings if f.level == lint.ERR)
        self.n_warn = len(findings) - self.n_err
        hidden = self.hidden_columns()
        inv = self.settings.value("inventory", "") or ""
        if inv and Path(inv).exists():
            self.model.inventory.load(Path(inv))
            if inv not in self.watcher.files():
                self.watcher.addPath(inv)
        self.inv_btn.setText(f"Inventory: {Path(inv).name}" if self.model.inventory.loaded else "Inventory…")
        if self.model.inventory.error:
            self.statusBar().showMessage(self.model.inventory.error, 8000)
        self.remember_columns()
        self.model.group = self.group_box.currentData() or ""
        self.model.load(parts, lint.findings_by_part(parts, findings))
        self.build_categories()
        self.model.set_group(self.proxy.group)
        self.after_columns_changed(hidden)
        if str(SYM_LIB) not in self.watcher.files():
            self.watcher.addPath(str(SYM_LIB))
        self.proxy.set_query(self.search.text())
        self.select(cur)
        self.update_status()

    def choose_inventory(self):
        from PyQt6.QtWidgets import QFileDialog
        start = self.settings.value("inventory", "") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Inventory.xlsx", start, "Excel (*.xlsx)")
        if path:
            old = self.settings.value("inventory", "")
            if old and old in self.watcher.files():
                self.watcher.removePath(old)
            self.settings.setValue("inventory", path)
            self.reload()

    def build_categories(self):
        """Fill the Group box; the Category box follows the chosen group."""
        cur = self.group_box.currentData() or ""
        counts: dict[str, int] = {}
        for p in self.model.parts:
            g = group_of(p.category)
            counts[g] = counts.get(g, 0) + 1
        self.group_box.blockSignals(True)
        self.group_box.clear()
        self.group_box.addItem(f"All groups   {len(self.model.parts)}", "")
        for g in sorted(counts):
            self.group_box.addItem(f"{g}   {counts[g]}", g)
        i = self.group_box.findData(cur)
        self.group_box.setCurrentIndex(max(i, 0))
        self.group_box.blockSignals(False)
        self.proxy.group = self.group_box.currentData() or ""
        self.fill_cat_box()

    def fill_cat_box(self):
        cur = self.cat_box.currentData() or ""
        g = self.proxy.group
        counts: dict[str, int] = {}
        for p in self.model.parts:
            if not g or group_of(p.category) == g:
                counts[p.category] = counts.get(p.category, 0) + 1
        self.cat_box.blockSignals(True)
        self.cat_box.clear()
        self.cat_box.addItem(f"All categories   {sum(counts.values())}", "")
        for c in sorted(counts, key=str.lower):
            self.cat_box.addItem(f"{c.strip('[]') or '(none)'}   {counts[c]}", c)
        i = self.cat_box.findData(cur)
        self.cat_box.setCurrentIndex(max(i, 0))
        self.cat_box.blockSignals(False)
        self.proxy.category = self.cat_box.currentData() or ""

    def group_changed(self):
        self.proxy.group = self.group_box.currentData() or ""
        self.fill_cat_box()
        self.apply_group()
        self.proxy.invalidateFilter()
        self.update_status()

    def remember_columns(self):
        if not self.model.columns:
            return
        for i, c in enumerate(self.model.columns):
            if not self.table.isColumnHidden(i) and self.table.columnWidth(i) > 0:
                self.col_widths[c] = self.table.columnWidth(i)
        hh = self.table.horizontalHeader()
        if 0 <= hh.sortIndicatorSection() < len(self.model.columns):
            self.sort_col = (self.model.columns[hh.sortIndicatorSection()], hh.sortIndicatorOrder())

    def apply_group(self):
        """Switch the table to the selected group's columns."""
        cur = self.current_name()
        hidden = self.hidden_columns()
        self.remember_columns()
        self.model.set_group(self.proxy.group)
        self.after_columns_changed(hidden)
        self.select(cur)

    def after_columns_changed(self, hidden: set[str]):
        self.build_cols_menu(hidden)
        for i, c in enumerate(self.model.columns):
            if c in self.col_widths:
                self.table.setColumnWidth(i, self.col_widths[c])
            else:
                self.table.resizeColumnToContents(i)
                self.table.setColumnWidth(i, min(max(self.table.columnWidth(i), 60), 260))
        name, order = self.sort_col
        if name in self.model.columns:
            self.table.sortByColumn(self.model.columns.index(name), order)

    def hidden_columns(self) -> set[str]:
        """Hidden columns by name. Columns not shown in the current group keep their old state."""
        if self.model.columns:
            now = {c for i, c in enumerate(self.model.columns) if self.table.isColumnHidden(i)}
            shown = {c for i, c in enumerate(self.model.columns) if not self.table.isColumnHidden(i)}
            self._hidden = (self._hidden - shown) | now
            return set(self._hidden)
        self._hidden = set(self.settings.value("hidden", ["Keywords"]) or [])
        return set(self._hidden)

    def build_cols_menu(self, hidden: set[str]):
        self.cols_menu.clear()
        for i, c in enumerate(self.model.columns):
            a = self.cols_menu.addAction(c)
            a.setCheckable(True)
            a.setChecked(c not in hidden)
            self.table.setColumnHidden(i, c in hidden)
            a.toggled.connect(lambda on, i=i: self.table.setColumnHidden(i, not on))
        self.cols_menu.addSeparator()
        self.cols_menu.addAction("Fit columns", self.fit_columns)

    def fit_columns(self):
        self.table.resizeColumnsToContents()
        for i in range(self.model.columnCount()):
            self.table.setColumnWidth(i, min(self.table.columnWidth(i), 320))

    # ---------------------------------------------------------------- events
    def toggle_issues(self, on):
        self.proxy.issues_only = on
        self.proxy.invalidateFilter()
        self.update_status()

    def cat_changed(self):
        self.proxy.category = self.cat_box.currentData() or ""
        self.proxy.invalidateFilter()
        self.update_status()

    def update_status(self):
        self.status.setText(f"{self.proxy.rowCount()} of {self.model.rowCount()} parts    "
                            f"library: {self.n_err} errors, {self.n_warn} warnings" + self.stock_status())

    def stock_status(self) -> str:
        if not self.model.inventory.loaded:
            return ""
        parts = [p for p in self.model.parts if not p.name.startswith(lint.VIRTUAL)]
        no_num = sum(1 for p in parts if not (self.model.stock(p) and self.model.stock(p).numbers))
        return f"    inventory: {no_num} of {len(parts)} parts without #"

    def part_at(self, proxy_idx) -> Part | None:
        if not proxy_idx.isValid():
            return None
        return self.model.parts[self.proxy.mapToSource(proxy_idx).row()]

    def current_name(self) -> str:
        p = self.part_at(self.table.currentIndex()) if self.model.parts else None
        return p.name if p else ""

    def select(self, name: str):
        if not name:
            return
        for r, p in enumerate(self.model.parts):
            if p.name == name:
                idx = self.proxy.mapFromSource(self.model.index(r, 0))
                if idx.isValid():
                    self.table.setCurrentIndex(idx)
                    self.table.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
                return

    def row_changed(self, cur, _prev):
        p = self.part_at(cur)
        if not p:
            return
        self.sym_view.show_prims(symbol_primitives(p.node), flip=True)
        self.claude.set_part(p)
        fp = load_footprint(p.footprint) if p.footprint else None
        self.fp_view.show_prims(footprint_primitives(fp) if fp else [], flip=False,
                                note="Footprint not found" if p.footprint else "No pepy_sym_lib footprint")
        self.info.setHtml(self.detail_html(p))

    def detail_html(self, p: Part) -> str:
        esc = lambda s: (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        rows = []
        for k, v in p.fields.items():
            vv = esc(v)
            if k == "Datasheet" and v.startswith("http"):
                vv = f'<a href="{esc(v)}">{esc(v[:60])}{"…" if len(v) > 60 else ""}</a>'
            if k == "LCSC Part" and v:
                vv = f'<a href="https://www.lcsc.com/product-detail/{esc(v)}.html">{esc(v)}</a>'
            rows.append(f"<tr><td style='color:#999;padding-right:10px'>{esc(k)}</td><td>{vv}</td></tr>")
        iss = self.model.issues.get(p.name, [])
        ih = "".join(
            f"<li><span style='color:{'#ff6b6b' if f.level == lint.ERR else '#e6c35c'}'>{f.level}</span> "
            f"{esc(f.rule)}{(' — ' + esc(f.detail)) if f.detail else ''}"
            f"{' <i>(footprint)</i>' if f.kind == 'footprint' else ''}</li>" for f in iss)
        folder, files = self.model.originals.get(p.name, (None, []))
        if files:
            oh = "".join(f"<li><a href='{QUrl.fromLocalFile(str(f)).toString()}'>{esc(f.name)}</a>"
                         f" <span style='color:#999'>{f.stat().st_size // 1024 or 1} kB</span></li>" for f in files)
            orig = (f"<p style='margin-top:10px'><b>Original files</b> — "
                    f"<a href='{QUrl.fromLocalFile(str(folder)).toString()}'>open folder</a></p>"
                    f"<ul style='margin-top:0'>{oh}</ul>")
        else:
            orig = (f"<p style='margin-top:10px;color:#ff6b6b'><b>No original files</b> in "
                    f"original_components/{esc(folder.name if folder else p.name)}</p>")
        stock = ""
        if self.model.inventory.loaded:
            st = self.model.stock(p)
            if not st or not st.numbers:
                stock = ("<p style='color:#ff6b6b;margin:2px 0'><b>Not in the inventory</b>"
                         + (f" <span style='color:#999'>(listed in row {', '.join(map(str, st.rows))} "
                            f"without #)</span>" if st else "") + "</p>")
            else:
                nums = f" · #{', #'.join(st.numbers)}" if st.numbers else " · no # yet"
                cnt = f"{st.pcs:g} pcs" if st.counted else "not counted yet"
                color = "#7c7" if st.counted and st.pcs > 0 else "#e6c35c"
                stock = (f"<p style='margin:2px 0'><b style='color:{color}'>Stock: {cnt}</b>"
                         f" <span style='color:#999'>(Inventory row {', '.join(map(str, st.rows))}{nums})</span></p>")
        return (f"<h3 style='margin:0'>{esc(p.name)}</h3>"
                f"<p style='color:#999;margin:2px 0 8px'>{LIB_NAME}:{esc(p.name)} · {len(p.pins)} pins</p>" + stock + 
                f"<table>{''.join(rows)}</table>" + orig
                + (f"<p style='margin-top:10px'><b>Issues</b></p><ul style='margin-top:0'>{ih}</ul>" if ih else
                   "<p style='color:#7c7'>No issues.</p>"))

    def double_click(self, idx):
        p = self.part_at(idx)
        col = self.model.columns[self.proxy.mapToSource(idx).column()]
        if col == "Datasheet" and p.fields.get("Datasheet", "").startswith("http"):
            webbrowser.open(p.fields["Datasheet"])
        elif col == "LCSC Part" and p.fields.get("LCSC Part"):
            webbrowser.open(f"https://www.lcsc.com/product-detail/{p.fields['LCSC Part']}.html")
        else:
            QGuiApplication.clipboard().setText(f"{LIB_NAME}:{p.name}")
            self.statusBar().showMessage(f"Copied {LIB_NAME}:{p.name}", 3000)

    def row_menu(self, pos):
        idx = self.table.indexAt(pos)
        p = self.part_at(idx)
        if not p:
            return
        clip = QGuiApplication.clipboard()
        m = QMenu(self)
        m.addAction("Copy symbol ID", lambda: clip.setText(f"{LIB_NAME}:{p.name}"))
        m.addAction("Copy cell", lambda: clip.setText(str(idx.data() or "")))
        if p.fields.get("LCSC Part"):
            m.addAction("Copy LCSC number", lambda: clip.setText(p.fields["LCSC Part"]))
            m.addAction("Open on LCSC", lambda: webbrowser.open(
                f"https://www.lcsc.com/product-detail/{p.fields['LCSC Part']}.html"))
        if p.fields.get("Datasheet", "").startswith("http"):
            m.addAction("Open datasheet", lambda: webbrowser.open(p.fields["Datasheet"]))
        folder, _ = self.model.originals.get(p.name, (ORIG_DIR / p.name, []))
        if folder.exists():
            m.addAction("Open component folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))
        m.addSeparator()
        m.addAction("Add part in this category…", lambda: self.open_add(p.category))
        m.addAction(f"Re-fetch {p.fields.get('LCSC Part', '')} (overwrite)…",
                    lambda: self.open_add(p.category, p, overwrite=True)).setEnabled(bool(p.fields.get("LCSC Part")))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def open_add(self, category: str = "", part: Part | None = None, overwrite: bool = False):
        if not isinstance(category, str):
            category = ""
        cats = sorted({p.category for p in self.model.parts if p.category}, key=str.lower)
        d = AddDialog(self, cats, category or (self.part_at(self.table.currentIndex()).category
                                               if self.table.currentIndex().isValid() else ""))
        if part:
            d.lcsc.setText(part.fields.get("LCSC Part", ""))
            d.part.setText(part.name[len(part.category):])
            d.overwrite.setChecked(overwrite)
        d.added.connect(lambda name: self.reload(select=name))
        d.exec()

    # ---------------------------------------------------------------- settings
    def restore(self):
        g = self.settings.value("geometry")
        if g:
            self.restoreGeometry(g)
        else:
            self.resize(1600, 900)
        for key, w in (("split2", self.split), ("right", self.right)):
            v = self.settings.value(key)
            if v:
                w.restoreState(v)
        self.search.setText(self.settings.value("search", "") or "")

    def closeEvent(self, e):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("split2", self.split.saveState())
        self.settings.setValue("right", self.right.saveState())
        self.remember_columns()
        self.settings.setValue("col_widths", json.dumps(self.col_widths))
        self.settings.setValue("sort_col", self.sort_col[0])
        self.settings.setValue("sort_order", int(self.sort_col[1].value))
        self.settings.setValue("hidden", sorted(self.hidden_columns()))
        self.settings.setValue("search", self.search.text())
        super().closeEvent(e)


def dark_palette(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    base, alt, text = QColor(32, 33, 36), QColor(40, 41, 45), QColor(222, 222, 222)
    for role, c in ((QPalette.ColorRole.Window, QColor(38, 39, 43)), (QPalette.ColorRole.WindowText, text),
                    (QPalette.ColorRole.Base, base), (QPalette.ColorRole.AlternateBase, alt),
                    (QPalette.ColorRole.Text, text), (QPalette.ColorRole.Button, QColor(48, 49, 54)),
                    (QPalette.ColorRole.ButtonText, text), (QPalette.ColorRole.Highlight, QColor(46, 98, 160)),
                    (QPalette.ColorRole.HighlightedText, QColor(255, 255, 255)),
                    (QPalette.ColorRole.ToolTipBase, QColor(50, 50, 55)), (QPalette.ColorRole.ToolTipText, text),
                    (QPalette.ColorRole.PlaceholderText, QColor(130, 130, 130)),
                    (QPalette.ColorRole.Link, QColor(110, 170, 255))):
        p.setColor(role, c)
    app.setPalette(p)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("pepy library browser")
    dark_palette(app)
    w = Main()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()