"""Build one transparent PNG per digit for the Rainmeter clock.

Pipeline:
  1. slice the sheet   -- 80x80 cells separated by 71px gaps, glyphs 2..9,0
  2. apply choices     -- per digit, either the sliced cell or one of the
                          standalone glyphs in source_digits/. "1" has no cell
                          on the sheet and must come from a file.
  3. key out the black -- exactly (0,0,0) becomes fully transparent, so
                          kerned/overlapping cells do not paint over their
                          neighbours' ink.
  4. snap to the ramp  -- standalone files are quantised separately and can
                          land a few units off the sheet's colours.

Which glyph each digit uses lives in config.json under "glyphs", so the GUI can
change it without editing code. Anything unlisted falls back to the sheet.
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).parent
SHEET = ROOT / "023456789.png"
SOURCES = ROOT / "source_digits"
OUT = ROOT / "rainmeter" / "AsciiClock" / "@Resources" / "digits"

CELL = 80
GAP = 71
ORDER = "234567890"
DIGITS = "0123456789"

# What a config entry says to use the sliced sheet cell rather than a file.
FROM_SHEET = "sheet"


def slice_sheet():
    """Cut the sheet into {digit: Image}."""
    sheet = Image.open(SHEET)
    expected = len(ORDER) * CELL + (len(ORDER) - 1) * GAP
    assert sheet.size == (expected, CELL), f"unexpected sheet size {sheet.size}"

    cells = {}
    for i, digit in enumerate(ORDER):
        x = i * (CELL + GAP)
        cells[digit] = sheet.crop((x, 0, x + CELL, CELL))

        # the gap after this cell should hold nothing but faint antialias spill
        if i < len(ORDER) - 1:
            gap = sheet.crop((x + CELL, 0, x + CELL + GAP, CELL)).convert("L")
            spill = [v for v in gap.get_flattened_data() if v]
            assert len(spill) < 10 and max(spill, default=0) < 128, (
                f"gap after {digit!r} holds real ink: {len(spill)} px, "
                f"max {max(spill)}"
            )
            if spill:
                print(f"  note: {len(spill)} faint px in gap after {digit!r}")
    return cells


def scan_sources():
    """Map each digit to the standalone glyph files that offer it.

    A file supplies the first digit appearing in its name, so both 1.png and
    1_alt.png offer digit 1 and the part after it is just a label. Files
    carrying four or more distinct digits are assumed to be sheets, not single
    glyphs, and are skipped.
    """
    found = {d: {} for d in DIGITS}
    if not SOURCES.exists():
        return found

    for path in sorted(SOURCES.glob("*.png")):
        seen = [c for c in path.stem if c.isdigit()]
        if len(set(seen)) >= 4:
            print(f"  note: {path.name} looks like a sheet, skipping")
            continue
        if not seen:
            print(f"  note: {path.name} names no digit, skipping")
            continue
        found[seen[0]][path.name] = path
    return found


def options_for(digit, sources, cells):
    """Every glyph that could serve this digit, sheet first if it exists."""
    opts = [FROM_SHEET] if digit in cells else []
    return opts + sorted(sources[digit])


def resolve_choices(sources, cells, wanted=None):
    """Pick a source per digit, falling back when the configured one is gone."""
    if wanted is None:
        # Local import: config.json is palette.py's to own, and importing it at
        # module scope would drag numpy into every asset rebuild.
        import palette
        wanted = palette.load_config().get("glyphs", {})

    chosen = {}
    for digit in DIGITS:
        opts = options_for(digit, sources, cells)
        assert opts, f"no source at all for digit {digit!r}"
        pick = wanted.get(digit)
        if pick not in opts:
            if pick is not None:
                print(f"  note: digit {digit!r} wanted {pick!r}, not available")
            pick = opts[0]
        chosen[digit] = pick
    return chosen


def apply_choices(cells, sources, chosen):
    """Swap in the selected standalone glyph wherever one is chosen."""
    for digit in DIGITS:
        pick = chosen[digit]
        if pick == FROM_SHEET:
            continue
        img = Image.open(sources[digit][pick])
        assert img.size == (CELL, CELL), (
            f"{pick} is {img.size}, expected {(CELL, CELL)}"
        )
        verb = "overrides" if digit in cells else "supplies"
        print(f"  {pick} {verb} digit {digit!r}")
        cells[digit] = img
    return cells


def luminance(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def sheet_ramp(cells):
    """The canonical colour ramp, darkest to lightest, taken from the sheet.

    The sheet is the authority on colour: standalone override files are
    quantised separately and can land a few units off it.
    """
    seen = set()
    for img in cells.values():
        for _, c in img.convert("RGBA").getcolors(1 << 24):
            if c[3] and c[:3] != (0, 0, 0):
                seen.add(c[:3])
    return sorted(seen, key=luminance)


def snap_to_ramp(img, ramp):
    """Force every opaque pixel onto the canonical ramp. Returns px moved."""
    px = img.load()
    w, h = img.size
    cache, moved = {}, 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if not a:
                continue
            c = (r, g, b)
            if c not in cache:
                cache[c] = min(
                    ramp,
                    key=lambda t: (t[0] - c[0]) ** 2
                    + (t[1] - c[1]) ** 2
                    + (t[2] - c[2]) ** 2,
                )
            if cache[c] != c:
                px[x, y] = (*cache[c], a)
                moved += 1
    return moved


def key_out_black(img):
    """Make exactly-(0,0,0) pixels transparent, leaving every other pixel."""
    rgba = img.convert("RGBA")
    px = rgba.load()
    w, h = rgba.size
    cleared = 0
    for y in range(h):
        for x in range(w):
            r, g, b, _ = px[x, y]
            if (r, g, b) == (0, 0, 0):
                px[x, y] = (0, 0, 0, 0)
                cleared += 1
    return rgba, cleared


def main():
    cells = slice_sheet()
    ramp = sheet_ramp(cells)
    print(f"  canonical ramp, {len(ramp)} steps: "
          + " ".join("#%02X%02X%02X" % c for c in ramp))

    sources = scan_sources()
    cells = apply_choices(cells, sources, resolve_choices(sources, cells))

    missing = [d for d in DIGITS if d not in cells]
    assert not missing, f"no source for digit(s) {missing}"

    OUT.mkdir(parents=True, exist_ok=True)
    for digit in DIGITS:
        rgba, cleared = key_out_black(cells[digit])
        moved = snap_to_ramp(rgba, ramp)
        rgba.save(OUT / f"{digit}.png")
        opaque = CELL * CELL - cleared
        note = f", {moved} px snapped to ramp" if moved else ""
        print(f"  {digit}.png  {opaque:5} visible px, "
              f"{cleared:5} transparent{note}")

    print(f"wrote {len(DIGITS)} files to {OUT}")

    # Sweep frames are derived from these PNGs, so they have to be rebuilt
    # whenever the digits change or they will be left in a stale palette.
    import morph
    morph.build_all()


if __name__ == "__main__":
    main()
