"""Re-centre the wallpaper ring so screen-centre alignment just works.

The source art has its ring 9px low in its own 1080px canvas. Windows "Fill"
scales 1920x1080 up to a 2880x1800 screen by 1.667x, which magnifies that to
~15px on screen -- enough to see, and enough to throw off anything else you
align to screen centre.

The ring's true centre is found by mirror symmetry rather than by ink bounding
box: the bbox is skewed ~3px by a single outlying lobe pixel, while the
symmetry axis and the ink centroid agree to within half a pixel.

Outputs:
  wallpaper_centered.png  1920x1080, integer shift only, no resampling.
                          Drop-in replacement; Windows still scales+crops it.
  wallpaper_screen.png    2880x1800, native screen resolution. Windows does no
                          scaling or cropping at all, so image pixel == screen
                          pixel and the ring centre is exactly screen centre.
"""

from math import sqrt
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).parent
SRC = ROOT / "wallpaper.png"
SCREEN = (2880, 1800)


def profiles(img):
    g = img.convert("L")
    w, h = g.size
    px = g.load()
    col = [0.0] * w
    row = [0.0] * h
    for y in range(h):
        for x in range(w):
            v = px[x, y]
            if v:
                col[x] += v
                row[y] += v
    return col, row


def symmetry_axis(profile, span_px=60):
    """Coordinate of the axis about which the profile is most symmetric."""
    up = []
    for i in range(len(profile) - 1):
        up.append(profile[i])
        up.append((profile[i] + profile[i + 1]) / 2.0)
    up.append(profile[-1])

    m = len(up)
    centre = (m - 1) / 2.0
    best, best_c = centre / 2.0, -1.0
    for step in range(-span_px * 2, span_px * 2 + 1):
        c = int(round(centre + step))
        k = min(c, m - 1 - c)
        if k < 200:
            continue
        dot = ls = rs = 0.0
        for j in range(1, k + 1):
            a, b = up[c - j], up[c + j]
            dot += a * b
            ls += a * a
            rs += b * b
        corr = dot / sqrt(ls * rs) if ls and rs else 0.0
        if corr > best_c:
            best_c, best = corr, c / 2.0
    return best, best_c


def ring_centre(img):
    col, row = profiles(img)
    cx, hc = symmetry_axis(col)
    cy, vc = symmetry_axis(row)
    print(f"  ring centre ({cx:.2f}, {cy:.2f})  "
          f"symmetry corr h={hc:.4f} v={vc:.4f}")
    return cx, cy


def main():
    src = Image.open(SRC).convert("RGB")
    w, h = src.size
    print(f"source {SRC.name} {w}x{h}")
    cx, cy = ring_centre(src)
    print(f"  offset from canvas centre: "
          f"({cx - w/2:+.2f}, {cy - h/2:+.2f})")

    # --- 1. same-size fix: integer shift, no resampling ---------------------
    dx, dy = round(w / 2 - cx), round(h / 2 - cy)
    same = Image.new("RGB", (w, h), (0, 0, 0))
    same.paste(src, (dx, dy))
    same.save(ROOT / "wallpaper_centered.png")
    print(f"\nwallpaper_centered.png  {w}x{h}, shifted ({dx:+d}, {dy:+d})")
    ring_centre(same)

    # --- 2. screen-native: no scaling or cropping by Windows ---------------
    sw, sh = SCREEN
    s = max(sw / w, sh / h)  # match the size Fill currently renders at
    scaled = src.resize((round(w * s), round(h * s)), Image.LANCZOS)
    sx, sy = cx * s, cy * s
    shot = Image.new("RGB", SCREEN, (0, 0, 0))
    px_, py_ = round(sw / 2 - sx), round(sh / 2 - sy)
    shot.paste(scaled, (px_, py_))
    shot.save(ROOT / "wallpaper_screen.png")
    print(f"\nwallpaper_screen.png    {sw}x{sh}, scaled {s:.4f}x, "
          f"pasted at ({px_}, {py_})")
    ring_centre(shot)
    print(f"  screen centre is ({sw/2}, {sh/2})")


if __name__ == "__main__":
    main()
