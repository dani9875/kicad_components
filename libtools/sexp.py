"""Minimal KiCad S-expression reader/writer."""
from __future__ import annotations

import re


class Str(str):
    """A string atom that was (or must be) quoted."""


_UNESC = {"n": "\n", "t": "\t", "r": "\r"}
_TOK = re.compile(r'\s*(\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+)')


def parse(text: str) -> list:
    """Parse text into nested lists. Quoted atoms become Str."""
    stack: list[list] = [[]]
    pos, n = 0, len(text)
    while pos < n:
        m = _TOK.match(text, pos)
        if not m:
            break
        t = m.group(1)
        pos = m.end()
        if t == "(":
            stack.append([])
        elif t == ")":
            node = stack.pop()
            stack[-1].append(node)
        elif t[0] == '"':
            stack[-1].append(Str(re.sub(r"\\(.)", lambda m: _UNESC.get(m.group(1), m.group(1)), t[1:-1])))
        else:
            stack[-1].append(t)
    return stack[0]


def atom(a) -> str:
    if isinstance(a, Str) or a == "" or re.search(r'[\s()"]', str(a)):
        return '"' + (str(a).replace("\\", "\\\\").replace('"', '\\"')
                      .replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")) + '"'
    return str(a)


def dump(node, ind: int = 0) -> str:
    """Serialise like KiCad 8+: leaf lists on one line, other lists one child per line."""
    tab = "\t" * ind
    if not any(isinstance(c, list) for c in node):
        return tab + "(" + " ".join(atom(c) for c in node) + ")"
    i = 0
    while i < len(node) and not isinstance(node[i], list):
        i += 1
    lines = [tab + "(" + " ".join(atom(c) for c in node[:i])]
    rest = node[i:]
    if all(isinstance(c, list) and c and c[0] == "xy" for c in rest):
        # KiCad wraps a point list once the line reaches column 99 (tabs count as 1).
        cur = ""
        for c in rest:
            cur = f"{cur} {dump(c)}" if cur else dump(c)
            if ind + 1 + len(cur) >= 99:
                lines.append("\t" * (ind + 1) + cur)
                cur = ""
        if cur:
            lines.append("\t" * (ind + 1) + cur)
    else:
        for c in rest:
            lines.append(dump(c, ind + 1) if isinstance(c, list) else "\t" * (ind + 1) + atom(c))
    lines.append(tab + ")")
    return "\n".join(lines)


def kids(node, name: str) -> list:
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def kid(node, name: str):
    for c in node:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def walk(node):
    """Yield every sub-list, depth first."""
    for c in node:
        if isinstance(c, list):
            yield c
            yield from walk(c)


def num(v) -> str:
    """Format a number the way KiCad does (no trailing zeros)."""
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def fnum(node, i: int, default: float = 0.0) -> float:
    try:
        return float(node[i])
    except (IndexError, TypeError, ValueError):
        return default