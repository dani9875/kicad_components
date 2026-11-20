#!/usr/bin/env python3
"""Add an LCSC part to pepy_sym_lib.

    ./add_component.py C780183 "[POWER_IND]BOURNS SRP1038A-150M"
    ./add_component.py C17414 "[RES_SMD_0805_2012Metric]UNI-ROYAL 0805W8F1002T5E"

A category with a shared footprint (original_components/[CAT]/[CAT].kicad_mod)
is detected automatically. --generic forces one.
"""
import argparse
import sys

from libtools.add import AddError, add_part


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lcsc", help="LCSC part number, e.g. C780183")
    ap.add_argument("name", help='Library name, e.g. "[POWER_IND]BOURNS SRP1038A-150M"')
    ap.add_argument("--generic", help="Force a shared-footprint category")
    ap.add_argument("--overwrite", action="store_true", help="Replace an existing part")
    ap.add_argument("--dry-run", action="store_true", help="Print the result, write nothing")
    a = ap.parse_args()
    try:
        r = add_part(a.lcsc, a.name, a.generic, a.overwrite, a.dry_run)
    except AddError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if a.dry_run:
        print(r.symbol_text)
        if r.footprint_text:
            print(r.footprint_text)
    print(f"\n  symbol:    {r.symbol_mode}\n  footprint: {r.footprint}\n  3D model:  {r.model}")
    if r.warnings:
        print("\nCheck:")
        for w in r.warnings:
            print(f"  - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
