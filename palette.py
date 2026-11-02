"""Colour presets shared by the wallpaper and the clock digits.

Both halves of the setup are quantised to a 7-step ramp plus a background --
opaque black for the wallpaper, full transparency for the digits. That shared
structure is what lets a single preset retheme both: a preset is just seven
replacement colours, applied by position in the ramp.

Position, not exact RGB, is the unit of mapping. It survives a preset being
applied on top of another (the on-disk ramp is simply re-read each time) and it
does not care that the source art was quantised in separate batches.

The wallpaper is always rebuilt from the pristine source art, so repeated
theming cannot accumulate error. The digits are recoloured in place, which is
safe for the same reason -- the mapping is a permutation of ramp positions.

Usage:
    python palette.py extract          rewrite palettes.json from the art
    python palette.py list             show every preset
    python palette.py apply <name>     retheme and set the wallpaper
"""

import argparse
import ctypes
import json
from colorsys import hls_to_rgb, rgb_to_hls
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).parent
PALETTES = ROOT / "palettes.json"
CONFIG = ROOT / "config.json"
DIGITS_DIR = ROOT / "rainmeter" / "AsciiClock" / "@Resources" / "digits"
# Local art, never published, wins over the repo's wallpaper.png. It has to share
# that file's ramp and ring centre, because palettes.json holds only one of each.
WALL_SRC = ROOT / "wallpaper_personal.png"
if not WALL_SRC.exists():
    WALL_SRC = ROOT / "wallpaper.png"
WALL_1080 = ROOT / "wallpaper_centered.png"
WALL_SCREEN = ROOT / "wallpaper_screen.png"

SCREEN = (2880, 1800)
RAMP_LEN = 7

# How the 1920x1080 art is enlarged to the 2880x1800 screen.
#   lanczos_quantize  smooth geometry, then snapped back onto the preset's own
#                     colours -- keeps the art at exactly 8 colours (default)
#   nearest           no new colours ever, but 1px strokes scale unevenly
#   lanczos           smoothest, but interpolates ~10k intermediate colours
RESAMPLERS = ("lanczos_quantize", "nearest", "lanczos")

DEFAULT_CONFIG = {
    "preset": "original_blue",
    "apply_to": {"wallpaper": True, "digits": True},
    "resample": "lanczos_quantize",
    # Which glyph each digit uses. "sheet" (or simply being absent) means the
    # cell sliced from 023456789.png; anything else names a file in
    # source_digits/. Digit 1 has no sheet cell, so it must name a file.
    "glyphs": {
        "1": "1_small_alt.png",
        "2": "2-alt-october.png",
        "5": "5-alt-october-3.png",
    },
    "rainmeter": {
        "TrimOne": 0,
        "Tracking": 0,
        "SepWidth": 40,
        "Scale": 1,
        "Debug": 0,
        "DebugSecPerMin": 5,
    },
}


# ------------------------------------------------------------------ colour --

def to_hex(c):
    return "#%02X%02X%02X" % tuple(int(v) for v in c)


def from_hex(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def luminance(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def nearest_index(c, ramp):
    return min(
        range(len(ramp)),
        key=lambda i: sum((ramp[i][k] - c[k]) ** 2 for k in range(3)),
    )


def shift_hue(ramp, target_deg, sat_mul=1.0):
    """Rotate a ramp so its mean hue lands on target_deg.

    Rotating rather than assigning keeps the ramp's internal hue drift -- the
    source blues run violet at the dark end and cyan at the light end, and that
    drift is what stops a themed ramp looking flat.
    """
    hls = [rgb_to_hls(*[v / 255 for v in c]) for c in ramp]
    mean_h = sum(h for h, _, _ in hls) / len(hls)
    delta = (target_deg / 360.0) - mean_h
    out = []
    for h, l, s in hls:
        r, g, b = hls_to_rgb((h + delta) % 1.0, l, min(1.0, s * sat_mul))
        out.append(tuple(max(0, min(255, round(v * 255))) for v in (r, g, b)))
    return out


# ----------------------------------------------------------------- reading --

def image_ramp(img, background=(0, 0, 0)):
    """Opaque, non-background colours in the image, darkest to lightest."""
    rgba = img.convert("RGBA")
    seen = {c[:3] for _, c in rgba.getcolors(1 << 24)
            if c[3] and c[:3] != background}
    return sorted(seen, key=luminance)


def digits_ramp():
    """The ramp the digit PNGs currently carry."""
    seen = set()
    for p in sorted(DIGITS_DIR.glob("[0-9].png")):
        rgba = Image.open(p).convert("RGBA")
        seen |= {c[:3] for _, c in rgba.getcolors(1 << 24) if c[3]}
    return sorted(seen, key=luminance)


def load_palettes():
    return json.loads(PALETTES.read_text())


def load_config():
    if not CONFIG.exists():
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    cfg = json.loads(CONFIG.read_text())
    for k, v in DEFAULT_CONFIG.items():          # tolerate older config files
        cfg.setdefault(k, v)
        if isinstance(v, dict):
            for kk, vv in v.items():
                cfg[k].setdefault(kk, vv)
    return cfg


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")


# -------------------------------------------------------------- extraction --

def extract():
    """Read the palettes out of the art and (re)write palettes.json."""
    wall = Image.open(WALL_SRC).convert("RGB")
    wall_ramp = image_ramp(wall)
    dig_ramp = digits_ramp()

    for label, ramp in (("wallpaper", wall_ramp), ("digits", dig_ramp)):
        assert len(ramp) == RAMP_LEN, (
            f"{label} has {len(ramp)} ramp steps, expected {RAMP_LEN}: "
            + " ".join(to_hex(c) for c in ramp)
        )

    # Cached so theming never has to re-run the (slow) symmetry search.
    import center_wallpaper
    cx, cy = center_wallpaper.ring_centre(wall)

    data = {
        "_comment": "Generated by palette.py extract -- edit presets freely.",
        "sources": {
            "wallpaper": {
                "file": WALL_SRC.name,
                "size": list(wall.size),
                "background": to_hex((0, 0, 0)),
                "ramp": [to_hex(c) for c in wall_ramp],
                "ring_centre": [round(cx, 2), round(cy, 2)],
            },
            "digits": {
                "dir": DIGITS_DIR.name,
                "background": "transparent",
                "ramp": [to_hex(c) for c in dig_ramp],
            },
        },
        "presets": {},
    }

    presets = data["presets"]
    presets["original_blue"] = {
        "label": "Original (wallpaper blue)",
        "ramp": [to_hex(c) for c in wall_ramp],
    }
    presets["original_violet"] = {
        "label": "Original (digit violet)",
        "ramp": [to_hex(c) for c in dig_ramp],
    }
    for name, label, hue, sat in (
        ("ember", "Ember", 8, 1.0),
        ("amber", "Amber", 38, 1.0),
        ("matrix", "Matrix", 122, 1.0),
        ("teal", "Teal", 176, 1.0),
        ("magenta", "Magenta", 308, 1.0),
        ("mono", "Mono", 0, 0.0),
    ):
        presets[name] = {
            "label": label,
            "ramp": [to_hex(c) for c in shift_hue(wall_ramp, hue, sat)],
        }

    PALETTES.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {PALETTES.name}")
    print(f"  wallpaper ramp  {' '.join(to_hex(c) for c in wall_ramp)}")
    print(f"  digits ramp     {' '.join(to_hex(c) for c in dig_ramp)}")
    print(f"  ring centre     ({cx:.2f}, {cy:.2f})")
    print(f"  {len(presets)} presets: {', '.join(presets)}")
    return data


# ---------------------------------------------------------------- theming --

def remap_rgb(arr, src_ramp, dst_ramp, src_bg=(0, 0, 0), dst_bg=(0, 0, 0)):
    """Map every colour to the destination ramp by its position in the source."""
    flat = arr.reshape(-1, 3)
    uniq, inv = np.unique(flat, axis=0, return_inverse=True)
    lut = np.empty_like(uniq)
    for i, c in enumerate(uniq):
        t = tuple(int(v) for v in c)
        lut[i] = dst_bg if t == src_bg else dst_ramp[nearest_index(t, src_ramp)]
    return lut[inv].reshape(arr.shape)


def quantize_to(img, palette):
    """Snap every pixel to its nearest colour in palette."""
    a = np.asarray(img).astype(np.int32)
    best_i = np.zeros(a.shape[:2], dtype=np.uint8)
    best_d = None
    for i, c in enumerate(palette):
        d = ((a - np.array(c, dtype=np.int32)) ** 2).sum(-1)
        if best_d is None:
            best_d = d
        else:
            m = d < best_d
            best_d = np.where(m, d, best_d)
            best_i = np.where(m, i, best_i)
    return Image.fromarray(np.array(palette, dtype=np.uint8)[best_i])


def theme_digits(dst_ramp):
    """Recolour the digit PNGs in place. Returns the number of files touched."""
    src_ramp = digits_ramp()
    assert len(src_ramp) == RAMP_LEN, (
        f"digits carry {len(src_ramp)} ramp steps, expected {RAMP_LEN}; "
        "re-run extract_digits.py"
    )
    unchanged = [tuple(c) for c in src_ramp] == [tuple(c) for c in dst_ramp]

    touched = 0
    for p in ([] if unchanged else sorted(DIGITS_DIR.glob("[0-9].png"))):
        rgba = Image.open(p).convert("RGBA")
        a = np.asarray(rgba)
        rgb, alpha = a[:, :, :3], a[:, :, 3]
        out = remap_rgb(rgb, src_ramp, dst_ramp)
        out = np.where(alpha[:, :, None] > 0, out, 0)
        stacked = np.dstack([out.astype(np.uint8), alpha])
        Image.fromarray(stacked, "RGBA").save(p)
        touched += 1

    # Rebuild rather than recolour: the sweep frames are pure composites of
    # these ten files, so regenerating is both simpler and cheaper than
    # remapping 1200 images, and it cannot drift out of sync. Also covers the
    # case where the palette is unchanged but the frames are simply missing.
    import morph
    if not unchanged or not morph.OUT.exists():
        morph.build_all(quiet=True)
    return touched


def theme_wallpaper(dst_ramp, dst_bg=(0, 0, 0), resample="lanczos_quantize"):
    """Rebuild both wallpaper renders from the pristine source, recoloured."""
    assert resample in RESAMPLERS, f"unknown resampler {resample!r}"
    pal = load_palettes()
    src = Image.open(WALL_SRC).convert("RGB")
    w, h = src.size

    src_ramp = [from_hex(c) for c in pal["sources"]["wallpaper"]["ramp"]]
    themed = Image.fromarray(
        remap_rgb(np.asarray(src), src_ramp, dst_ramp, (0, 0, 0), dst_bg)
        .astype(np.uint8)
    )

    # Centre the ring first, by integer shift only, so no resampling happens
    # before the art is already in the right place.
    cx, cy = pal["sources"]["wallpaper"]["ring_centre"]
    dx, dy = round(w / 2 - cx), round(h / 2 - cy)
    centred = Image.new("RGB", (w, h), dst_bg)
    centred.paste(themed, (dx, dy))
    centred.save(WALL_1080)

    sw, sh = SCREEN
    s = max(sw / w, sh / h)
    size = (round(w * s), round(h * s))
    filt = Image.NEAREST if resample == "nearest" else Image.LANCZOS
    scaled = centred.resize(size, filt)

    shot = Image.new("RGB", SCREEN, dst_bg)
    shot.paste(scaled, ((sw - size[0]) // 2, (sh - size[1]) // 2))
    if resample == "lanczos_quantize":
        shot = quantize_to(shot, [tuple(dst_bg)] + [tuple(c) for c in dst_ramp])
    shot.save(WALL_SCREEN)

    n = len(shot.convert("RGB").getcolors(1 << 24))
    return {"shift": (dx, dy), "scale": s, "colours": n}


def set_wallpaper(path=WALL_SCREEN):
    """Tell Windows to use the rendered wallpaper (SPI_SETDESKWALLPAPER)."""
    ok = ctypes.windll.user32.SystemParametersInfoW(
        0x0014, 0, str(Path(path).resolve()), 0x01 | 0x02
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "SystemParametersInfoW failed")
    return True


def apply_preset(name, wallpaper=None, digits=None, resample=None,
                 set_desktop=True, remember=True):
    """Apply a preset to the wallpaper, the digits, or both.

    remember=False leaves config.json alone -- for internal re-applies, which
    would otherwise rewrite apply_to and clobber the user's own choices.
    """
    pal = load_palettes()
    if name not in pal["presets"]:
        raise KeyError(f"no preset {name!r}; have {', '.join(pal['presets'])}")
    cfg = load_config()
    wallpaper = cfg["apply_to"]["wallpaper"] if wallpaper is None else wallpaper
    digits = cfg["apply_to"]["digits"] if digits is None else digits
    resample = resample or cfg["resample"]

    ramp = [from_hex(c) for c in pal["presets"][name]["ramp"]]
    report = {"preset": name, "ramp": [to_hex(c) for c in ramp]}

    if digits:
        report["digits"] = theme_digits(ramp)
        # The date line belongs to the clock, so it tracks the digit palette:
        # brightest ramp entry, written into the skin file so it survives a
        # refresh. Ramps are stored darkest-first, hence [-1].
        import skin
        skin.write_vars({"TextColor": "%d,%d,%d" % tuple(ramp[-1])})
        report["text_colour"] = to_hex(ramp[-1])
    if wallpaper:
        report["wallpaper"] = theme_wallpaper(ramp, (0, 0, 0), resample)
        if set_desktop:
            set_wallpaper()
            report["desktop"] = "set"

    if remember:
        cfg["preset"] = name
        cfg["apply_to"] = {"wallpaper": bool(wallpaper), "digits": bool(digits)}
        cfg["resample"] = resample
        save_config(cfg)
    return report


# -------------------------------------------------------------------- cli --

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("extract")
    sub.add_parser("list")
    a = sub.add_parser("apply")
    a.add_argument("name")
    a.add_argument("--wallpaper-only", action="store_true")
    a.add_argument("--digits-only", action="store_true")
    a.add_argument("--resample", choices=RESAMPLERS)
    args = ap.parse_args()

    if args.cmd == "extract":
        extract()
    elif args.cmd == "list":
        pal, cfg = load_palettes(), load_config()
        for name, p in pal["presets"].items():
            mark = "*" if name == cfg["preset"] else " "
            print(f" {mark} {name:16} {p['label']:26} {' '.join(p['ramp'])}")
        print("\n * = currently applied")
    else:
        rep = apply_preset(
            args.name,
            wallpaper=not args.digits_only,
            digits=not args.wallpaper_only,
            resample=args.resample,
        )
        for k, v in rep.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
