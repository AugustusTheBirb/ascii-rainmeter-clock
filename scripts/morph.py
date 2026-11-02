"""Frames for the clock's cell-by-cell sweep.

A digit that changes does not snap to its next value -- it dissolves into it a
few cells at a time, sweeping left-to-right and bottom-up. Every position does
this, so 00:39 -> 00:40 sweeps the 3 and the 9 together.

Only thirteen transitions exist across all four positions (see transitions):
the ten d -> (d+1) % 10 steps plus the wraps that only some positions make. At
60 frames each and two cell sizes that is ~1600 small PNGs, which is cheap
enough that pre-rendering beats compositing live in Rainmeter.

Transitions live in <cell>/<old>_<new>/. A digit that is not about to change is
pinned to frame 0 by the skin, so <cell>/<d>_<d>/ holds just 0.png.

60 is the resolution of the *asset*, not the pacing: the skin decides how many
of the frames it actually shows, from its tick rate and the length of the sweep
window. See reveal_order for why only changed cells are counted.

Frame 0 is the pure old digit and frame 59 the pure new one, so the handover at
:59 -> :00 is seamless: the last frame of one transition and the first frame of
the next are the same image. It also means frame 0 alone is a valid "sweep off"
rendering, which is how the skin disables the effect without branching.

Cell sizes: the glyphs sit on a 4px character grid (measured -- 4px at offset
(0,0) minimises distinct tile patterns, and 8px only looks aligned because it is
a multiple of 4). 8 therefore moves a 2x2 block of characters per step and 4
moves exactly one, and both land on real character boundaries.
"""

import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).parent.parent
DIGITS_DIR = ROOT / "rainmeter" / "AsciiClock" / "@Resources" / "digits"
OUT = DIGITS_DIR / "morph"

SIZE = 80
CELLS = (8, 4)
FRAMES = 60


def reveal_order(cell, old=None, new=None, size=SIZE):
    """Cell boxes in sweep order: bottom row first, left to right within a row.

    Given both glyphs, cells that are identical in the two are dropped. Pasting
    one changes nothing on screen, so a step that landed on it would read as the
    sweep having stalled -- and between two 80x80 glyphs 28% of the 8px cells
    and 50% of the 4px cells are such no-ops, nearly all of them empty
    background. Dropping them makes every step a visible one and shortens the
    reveal, so each step can be smaller for the same duration.
    """
    n = size // cell
    boxes = [
        (c * cell, r * cell, c * cell + cell, r * cell + cell)
        for r in range(n - 1, -1, -1)
        for c in range(n)
    ]
    if old is None or new is None:
        return boxes
    return [b for b in boxes if old.crop(b).tobytes() != new.crop(b).tobytes()]


def transitions():
    """Every (old, new) digit pair a 24h HH:MM clock shows in any one position.

    Derived by walking the whole day rather than listed by hand: beyond the ten
    d -> d+1 steps there are wraps that only certain positions make -- 5->0 in
    the tens of minutes, 3->0 and 2->0 at 23:59 -> 00:00.
    """
    def hhmm(m):
        return f"{m // 60 % 24:02d}{m % 60:02d}"

    pairs = set()
    for m in range(24 * 60):
        pairs.update((int(a), int(b))
                     for a, b in zip(hhmm(m), hhmm(m + 1)) if a != b)
    return sorted(pairs)


def transition_frames(old, new, cell):
    """The 60 frames dissolving `old` into `new`.

    Built incrementally -- each frame starts from the previous one and only the
    newly revealed cells are pasted -- so the work is O(cells), not
    O(frames x cells).
    """
    order = reveal_order(cell, old, new)
    total = len(order)
    frames, img, done = [], old.copy(), 0

    for k in range(FRAMES):
        target = k * total // (FRAMES - 1)
        for box in order[done:target]:
            # paste without a mask: pixels are replaced, alpha included.
            # alpha_composite would leave old ink showing wherever the new
            # glyph is blank.
            img.paste(new.crop(box), box)
        done = target
        frames.append(img.copy())
    return frames


def build_all(quiet=False):
    """Regenerate every frame from the digit PNGs as they currently are."""
    digits = {
        d: Image.open(DIGITS_DIR / f"{d}.png").convert("RGBA") for d in range(10)
    }
    for d, img in digits.items():
        assert img.size == (SIZE, SIZE), f"digit {d} is {img.size}"

    if OUT.exists():
        shutil.rmtree(OUT)

    pairs = transitions()
    written = 0
    for cell in CELLS:
        for old, new in pairs:
            frames = transition_frames(digits[old], digits[new], cell)
            dest = OUT / str(cell) / f"{old}_{new}"
            dest.mkdir(parents=True, exist_ok=True)
            for k, frame in enumerate(frames):
                frame.save(dest / f"{k}.png")
                written += 1
        # Unchanging digits: the skin pins them to frame 0, so that is all
        # they need.
        for d in range(10):
            dest = OUT / str(cell) / f"{d}_{d}"
            dest.mkdir(parents=True, exist_ok=True)
            digits[d].save(dest / "0.png")
            written += 1

    if not quiet:
        cells = ", ".join(
            f"{c}px = {changed_cells(c, digits)} of {(SIZE // c) ** 2} cells"
            for c in CELLS
        )
        print(f"  morph: {written} frames, {len(pairs)} transitions "
              f"(mean changed per transition: {cells})")
    return written


def changed_cells(cell, digits=None):
    """Mean number of cells a transition actually alters, at this cell size.

    The skin's pacing is quoted against this rather than the full grid, since
    the unchanged cells are no longer part of the reveal.
    """
    if digits is None:
        digits = {
            d: Image.open(DIGITS_DIR / f"{d}.png").convert("RGBA")
            for d in range(10)
        }
    counts = [len(reveal_order(cell, digits[d], digits[(d + 1) % 10]))
              for d in range(10)]
    return round(sum(counts) / len(counts))


if __name__ == "__main__":
    build_all()
