# pepy_sym_lib — KiCad component library

Personal KiCad library. Parts come from LCSC/EasyEDA and are cleaned up automatically.
The library files are in KiCad 10 format.

## Setup

```bash
git clone git@github.com:dani9875/kicad_components.git
cd kicad_components
pip install -r requirements.txt
```

In KiCad:

1. **Preferences → Manage Symbol Libraries**: add `pepy_sym_lib.kicad_sym`.
2. **Preferences → Manage Footprint Libraries**: add the `pepy_sym_lib.pretty` folder.
3. **Preferences → Configure Paths**: add `ORIGINAL_COMPONENTS` and point it to the `original_components` folder.

## Library browser

```bash
./library_browser.py
```

- Every field is a column. Right-click the header to show or hide columns.
- Pick one or more categories on the left (Ctrl/Shift-click).
- Red cells are fields that a part of that type must have.
- The right side shows the symbol, the footprint and all lint findings for the selected part.
- Double-click a part to copy `pepy_sym_lib:<name>`. Double-click Datasheet or LCSC Part to open the page.
- **Add part…** (Ctrl+N) adds a part from LCSC. The browser reloads when the library files change.

Search syntax (all terms must match):

```
10k                  text anywhere in the row
tol:1%               field contains text (column name prefix or alias)
nvalue>=10k          numeric compare on engineering values: > < >= <= =
missing:tolerance    field is empty
has:dielectric       field is filled
is:error  is:warn    parts with lint findings
-murata              exclude
"two words"          phrase
```

Aliases: `ref val nv tol mfr lcsc desc fp pkg cat kw volt`.

## Add a part from the command line

```bash
./add_component.py C780183 "[POWER_IND]BOURNS SRP1038A-150M"
```

Name format: `[CATEGORY]Manufacturer MPN`.

- If a part in the same `[CATEGORY]` has the same pins, the new symbol is a copy of it with the new part data.
- If `original_components/[CATEGORY]/[CATEGORY].kicad_mod` exists, the part uses that shared footprint (resistors, capacitors).
- Otherwise the EasyEDA symbol and footprint are cleaned: standard fields, hidden data fields, pin types, footprint attribute, courtyard, `${REFERENCE}` on F.Fab, 3D path through `${ORIGINAL_COMPONENTS}`.
- The script prints a **Check** list at the end. If the list is empty, the part is ready.

Options: `--dry-run` (write nothing), `--overwrite` (replace a part), `--generic "[CAT]"` (force a shared footprint).

EasyEDA and LCSC responses are cached in `.easyeda_cache/`.

## Fields

- **Value**: manufacturer part number.
- **Nvalue**: the value shown on the schematic for R, C, L, FB (`10k`, `100nF`, `15uH`).
- **Tolerance** (`±1%`), **Voltage rating**, **Power**, **Dielectric**, **Rated current**: filled from LCSC data when available. A field without data is left out.
- **Manufacturer**: without the Chinese brand suffix.
- **Description**, **Datasheet**, **LCSC Part**, **Package**, **ki_keywords**.

## Library check

```bash
./lint_library.py        # summary, exit code 1 on errors
./lint_library.py -v     # every finding
```

GitHub Actions runs the same check on every push.

## Manually handled components

Test points and mounting holes are handled manually. Their symbols are the same, but the footprints vary.

## Release notes

### Version: v6
Changelog:
- AM625
- DN007 antenna
- Low voltage boost
- Many 0402 inductors
- U.FL connector
- Zero ohm and 1x2 screw terminal 
- PMW336DM
- Bunch of other capactiors and resistors

### Version: v5
Changelog:
- Rotation change for IP428
- Change to relative path

### Version: v4
Changelog:
- 10P 2x5 1.27 mm pin header added to library

### Version: v3
Changelog:
- 10 nF YAGEO capacitor added
- 100 MOhm Visay resistor added
- Resistor with 1206 size (symbol and footprint) added

### Version: v2
Changelog:
- Param generic resistor/capacitor compnent
- FUSE added (generic)
- Adding information to many components

### Version: v1
Changelog:
- Initial component library