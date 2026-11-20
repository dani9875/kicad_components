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

import math
import re
import shlex
import sys
import webbrowser

from PyQt6.QtCore import (QAbstractTableModel, QFileSystemWatcher, QModelIndex, QPointF, QRectF,
                          QSettings, QSortFilterProxyModel, Qt, QThread, QTimer, QUrl, pyqtSignal)
from PyQt6.QtGui import (QAction, QBrush, QColor, QDesktopServices, QFont, QGuiApplication,
                         QKeySequence, QPainter, QPalette, QPen, QPolygonF)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                             QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                             QPlainTextEdit, QPushButton, QSplitter, QTableView,
                             QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from libtools import lint
from libtools.add import AddError, add_part
from libtools.library import (FP_DIR, LIB_NAME, ORIG_DIR, SYM_LIB, Part, eng_to_float,
                              footprint_primitives, load_footprint, load_parts, symbol_primitives)

# --------------------------------------------------------------------------
# Columns
# --------------------------------------------------------------------------
LEAD_COLUMNS = ["Part", "Category", "Reference", "Value", "Nvalue", "Tolerance", "Voltage rating",
                "Power", "Dielectric", "Rated current", "Package", "Manufacturer", "LCSC Part",
                "Description", "Footprint", "Datasheet", "Keywords"]
FIELD_FOR_COLUMN = {"Keywords": "ki_keywords"}
ALIASES = {"ref": "Reference", "val": "Value", "nv": "Nvalue", "tol": "Tolerance",
           "mfr": "Manufacturer", "lcsc": "LCSC Part", "desc": "Description", "fp": "Footprint",
           "pkg": "Package", "cat": "Category", "kw": "Keywords", "volt": "Voltage rating"}
ENG_COLUMNS = {"Nvalue", "Voltage rating", "Power", "Rated current"}
ISSUES = "Issues"


def expected_columns(p: Part) -> set[str]:
    """Columns that should not be empty for this part (red when empty)."""
    if p.name.startswith(lint.VIRTUAL):
        return set()
    need = {"Description", "Manufacturer", "LCSC Part", "Datasheet", "Footprint"}
    if p.ref in ("R", "C", "L", "FB"):
        need |= {"Nvalue", "Tolerance"}
    if p.ref == "C":
        need |= {"Voltage rating"}
    return need


class PartsModel(QAbstractTableModel):
    SORT_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self):
        super().__init__()
        self.parts: list[Part] = []
        self.columns: list[str] = []
        self.issues: dict[str, list[lint.Finding]] = {}

    def load(self, parts, issues):
        self.beginResetModel()
        self.parts, self.issues = parts, issues
        extra = {}
        for p in parts:
            for k in p.fields:
                if k not in LEAD_COLUMNS and k not in FIELD_FOR_COLUMN.values():
                    extra[k] = extra.get(k, 0) + 1
        self.columns = LEAD_COLUMNS + sorted(extra, key=lambda k: -extra[k]) + [ISSUES]
        self.endResetModel()

    def value(self, p: Part, col: str) -> str:
        if col == "Part":
            return p.name[len(p.category):] or p.name
        if col == "Category":
            return p.category.strip("[]")
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
            if col == ISSUES:
                return "\n".join(f"{f.level}: {f.rule}" + (f" ({f.detail})" if f.detail else "")
                                 for f in self.issues.get(p.name, []))
            return v if len(v) > 30 else None
        if role == Qt.ItemDataRole.BackgroundRole:
            if not v and col in expected_columns(p):
                return QBrush(QColor(110, 40, 40))
            if col == ISSUES and v:
                err = any(f.level == lint.ERR for f in self.issues.get(p.name, []))
                return QBrush(QColor(130, 35, 35) if err else QColor(95, 80, 30))
        if role == Qt.ItemDataRole.ForegroundRole and col == "Datasheet" and v:
            return QBrush(QColor(110, 170, 255))
        if role == self.SORT_ROLE:
            if col in ENG_COLUMNS:
                f = eng_to_float(v)
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
        self.categories: set[str] = set()
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
        if self.categories and p.category not in self.categories:
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
        self.part.setPlaceholderText("Manufacturer MPN")
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
        if not self.lcsc.text().strip() or not self.part.text().strip():
            self.log.appendPlainText("Enter the LCSC number and the part name.")
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
        self.log.appendPlainText(f"symbol:    {r.symbol_mode}\nfootprint: {r.footprint}\n3D model:  {r.model}")
        if r.warnings:
            self.log.appendPlainText("Check:\n" + "\n".join(f"  - {w}" for w in r.warnings))
        if self.dry.isChecked():
            self.log.appendPlainText("\n" + r.symbol_text)
        else:
            self.added.emit(r.name)


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
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.issues_only)
        top.addWidget(cols)
        top.addWidget(reload_btn)
        top.addWidget(add_btn)

        # categories
        self.cats = QListWidget()
        self.cats.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.cats.itemSelectionChanged.connect(self.cat_changed)

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
        right.addWidget(self.info)
        right.setSizes([260, 260, 300])

        self.split = QSplitter()
        self.split.addWidget(self.cats)
        self.split.addWidget(self.table)
        self.split.addWidget(right)
        self.split.setSizes([200, 1000, 380])
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
        self.model.load(parts, lint.findings_by_part(parts, findings))
        self.build_cols_menu(hidden)
        self.build_categories()
        if str(SYM_LIB) not in self.watcher.files():
            self.watcher.addPath(str(SYM_LIB))
        self.proxy.set_query(self.search.text())
        self.select(cur)
        self.update_status()

    def build_categories(self):
        selected = {i.data(Qt.ItemDataRole.UserRole) for i in self.cats.selectedItems()}
        counts: dict[str, int] = {}
        for p in self.model.parts:
            counts[p.category] = counts.get(p.category, 0) + 1
        self.cats.blockSignals(True)
        self.cats.clear()
        for c in sorted(counts, key=str.lower):
            it = QListWidgetItem(f"{c.strip('[]') or '(none)'}   {counts[c]}")
            it.setData(Qt.ItemDataRole.UserRole, c)
            self.cats.addItem(it)
            if c in selected:
                it.setSelected(True)
        self.cats.blockSignals(False)
        self.proxy.categories = {c for c in selected if c in counts}

    def hidden_columns(self) -> set[str]:
        if self.model.columns:
            return {c for i, c in enumerate(self.model.columns) if self.table.isColumnHidden(i)}
        return set(self.settings.value("hidden", ["Keywords"]) or [])

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
        self.proxy.categories = {i.data(Qt.ItemDataRole.UserRole) for i in self.cats.selectedItems()}
        self.proxy.invalidateFilter()
        self.update_status()

    def update_status(self):
        self.status.setText(f"{self.proxy.rowCount()} of {self.model.rowCount()} parts    "
                            f"library: {self.n_err} errors, {self.n_warn} warnings")

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
        return (f"<h3 style='margin:0'>{esc(p.name)}</h3>"
                f"<p style='color:#999;margin:2px 0 8px'>{LIB_NAME}:{esc(p.name)} · {len(p.pins)} pins</p>"
                f"<table>{''.join(rows)}</table>"
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
        folder = ORIG_DIR / (p.footprint or p.name)
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
        for key, w in (("split", self.split), ("right", self.right)):
            v = self.settings.value(key)
            if v:
                w.restoreState(v)
        h = self.settings.value("header")
        if h:
            self.table.horizontalHeader().restoreState(h)
        else:
            self.fit_columns()
            self.table.sortByColumn(self.model.columns.index("Category"), Qt.SortOrder.AscendingOrder)
        self.search.setText(self.settings.value("search", "") or "")

    def closeEvent(self, e):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("split", self.split.saveState())
        self.settings.setValue("right", self.right.saveState())
        self.settings.setValue("header", self.table.horizontalHeader().saveState())
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
