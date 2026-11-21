#!/usr/bin/env python3
"""Check pepy_sym_lib. Exit code 1 if any ERROR exists.

    ./lint_library.py              summary; errors listed in full
    ./lint_library.py -v           list every finding
    ./lint_library.py --only "[LDO]"
"""
import argparse
import collections
import sys

from libtools import lint


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--only", default="", help="Only items whose name starts with this")
    a = ap.parse_args()

    groups = collections.defaultdict(list)
    for f in lint.run():
        if f.item.startswith(a.only):
            groups[(f.level, f.rule)].append(f)
    errors = 0
    for (level, rule), items in sorted(groups.items(), key=lambda kv: (kv[0][0] != lint.ERR, -len(kv[1]))):
        errors += len(items) if level == lint.ERR else 0
        print(f"{level:5} {len(items):4}  {rule}")
        if a.verbose or level == lint.ERR:
            for f in items:
                print(f"             {f.item}" + (f"  ({f.detail})" if f.detail else ""))
    print(f"\n{errors} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
