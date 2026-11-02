"""Generate a ring of DNA from nothing but geometry.

A double helix has its axis bent into a circle and is viewed head-on. Each
backbone is a real 3D band with a flat rectangular cross-section, lying against
the helix like a ribbon wound round a cylinder. As it twists it turns its flat
face to the viewer and then its edge, so it looks wide where it crosses the
front and thin at the rim of the helix -- that comes from the projection, not
from any width being set by hand. No source image is involved.

Everything is sampled as points on those surfaces and projected straight onto
the screen through a depth buffer, so whatever is nearest wins.

Outputs:
  wallpaper.png    1920x1080, already ASCII: 8x8 characters in the wallpaper
                   ramp on black, ready for palette.py.
  ring_source.png  The same scene as smooth greyscale, for feeding through an
                   external ASCII filter instead.
"""

from pathlib import Path

import numpy as np
from PIL import Image

import palette

ROOT = Path(__file__).parent.parent
OUT_ASCII = ROOT / "art" / "wallpaper.png"
OUT_SOURCE = ROOT / "build" / "ring_source.png"

WIDTH, HEIGHT = 1920, 1080
CELL = 8

RING_R = 310        # ring axis radius, px
HELIX_R = 85        # helix radius around that axis, px
TWISTS = 5          # full helix turns per ring; the strands cross twice in each
GROOVE = np.pi      # angle between the strands; pi leaves no major or minor groove
BAND_W = 14.4       # backbone half-width, across its flat face, px
BAND_T = 1.6        # backbone half-thickness, px
PAIRS = 60          # base pairs round the ring; a multiple of 2 * TWISTS keeps every segment alike
PAIR_W = 2.5        # base pair half-width, px

SHADOW = 0.52       # brightness of the far side of the helix, 0-1
ASCII_GAMMA = 0.55  # under 1 lifts the mid-tones of the ASCII render

SAMPLE = 0.5        # spacing of surface samples, px; must stay under 1
RING_STEPS = 16000  # samples round the ring, fine enough for SAMPLE

# A cell needs this much ink to draw a character at all, and this much backbone
# to count as solid rather than as a line running through it.
MIN_INK = 0.32
SOLID = 0.65

EMPTY, BAND, PAIR = 0, 1, 2

# 8x8 bitmaps, one byte per row, most significant bit on the left.
GLYPHS = {
    "-": (0x00, 0x00, 0x00, 0x00, 0x7E, 0x00, 0x00, 0x00),
    "|": (0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x00),
    "/": (0x03, 0x06, 0x0C, 0x18, 0x30, 0x60, 0xC0, 0x00),
    "\\": (0xC0, 0x60, 0x30, 0x18, 0x0C, 0x06, 0x03, 0x00),
    "@": (0x3C, 0x66, 0x6E, 0x6A, 0x6E, 0x60, 0x3E, 0x00),
    "#": (0x24, 0x24, 0x7E, 0x24, 0x7E, 0x24, 0x24, 0x00),
    "G": (0x3C, 0x40, 0x40, 0x4E, 0x42, 0x42, 0x3C, 0x00),
    "o": (0x00, 0x00, 0x3C, 0x42, 0x42, 0x42, 0x3C, 0x00),
    ":": (0x00, 0x18, 0x18, 0x00, 0x18, 0x18, 0x00, 0x00),
}
DENSITY = ":oG#@"           # solid backbone, faintest to brightest
ORIENTED = "-\\|/"          # line directions, in 45 degree steps


def depth_light(z):
    """Brightness from depth: the far side of the helix falls into shadow."""
    return SHADOW + (1 - SHADOW) * np.clip((z / HELIX_R + 1) / 2, 0, 1) ** 1.3


def strand_frame(theta, offset):
    """A backbone's centre line and its orientation, as Nx3 arrays.

    Returns the centre, then three perpendicular unit vectors: along the
    strand, across its flat face, and out through that face.
    """
    phi = TWISTS * theta + offset
    zeros = np.zeros_like(theta)
    radial = np.stack([np.cos(theta), np.sin(theta), zeros], axis=-1)
    tangent = np.stack([-np.sin(theta), np.cos(theta), zeros], axis=-1)
    axis = np.stack([zeros, zeros, np.ones_like(theta)], axis=-1)

    cos_phi, sin_phi = np.cos(phi)[..., None], np.sin(phi)[..., None]
    out = cos_phi * radial + sin_phi * axis
    centre = RING_R * radial + HELIX_R * out

    # The derivative of centre with respect to theta.
    along = ((RING_R + HELIX_R * cos_phi) * tangent
             + HELIX_R * TWISTS * (cos_phi * axis - sin_phi * radial))
    along /= np.linalg.norm(along, axis=-1, keepdims=True)
    return centre, along, np.cross(out, along), out


def oriented(vectors):
    """Flip 3D vectors horizontally and turn them 90 degrees clockwise.

    Applied to the geometry rather than the finished image, because turning a
    rendered picture would leave its characters mirrored and on their sides.
    """
    return -vectors[..., 1], -vectors[..., 0], vectors[..., 2]


def to_screen(points):
    """Project 3D points to pixels, with the ring on screen centre."""
    x, y, z = oriented(points)
    return x + WIDTH / 2, y + HEIGHT / 2, z


class Canvas:
    """Depth-buffered pixel arrays that surfaces are splatted into."""

    def __init__(self):
        shape = (HEIGHT, WIDTH)
        self.depth = np.full(shape, -np.inf, dtype=np.float32)
        self.light = np.zeros(shape, dtype=np.float32)
        self.kind = np.zeros(shape, dtype=np.uint8)
        self.angle = np.zeros(shape, dtype=np.float32)

    def splat(self, points, light, kind, direction, bias=0):
        """Add surface samples, keeping the nearest at each pixel.

        direction holds the 3D direction of the line the surface runs along,
        which becomes the character orientation. bias nudges depth, so that a
        rung can tuck behind the backbone it meets at the same depth.
        """
        x, y, z = to_screen(points)
        dx, dy, _ = oriented(direction)
        z = z + bias
        cols, rows = x.astype(np.int32).ravel(), y.astype(np.int32).ravel()
        z = z.ravel()

        keep = z > self.depth[rows, cols]
        order = np.argsort(z[keep])     # nearest last, so it is written last
        rows, cols = rows[keep][order], cols[keep][order]

        self.depth[rows, cols] = z[keep][order]
        self.light[rows, cols] = light.ravel()[keep][order]
        self.kind[rows, cols] = kind
        # Image y runs down, which is what the glyph table expects.
        self.angle[rows, cols] = np.arctan2(dy, dx).ravel()[keep][order]


def spread(half_width):
    """Sample positions across -1..1, spaced SAMPLE px apart at that width."""
    return np.linspace(-1, 1, max(2, int(2 * half_width / SAMPLE) + 1))


def draw_band(canvas, offset):
    """One backbone: the four long faces of a twisted rectangular band."""
    theta = np.linspace(0, 2 * np.pi, RING_STEPS, endpoint=False)
    centre, along, across, out = strand_frame(theta, offset)

    faces = [
        (out, across, BAND_T, BAND_W),      # the two flat faces
        (-out, across, BAND_T, BAND_W),
        (across, out, BAND_W, BAND_T),      # the two narrow edges
        (-across, out, BAND_W, BAND_T),
    ]
    for normal, span, lift, half in faces:
        # A face turned away from the viewer cannot be seen.
        facing = normal[:, 2] > 0
        c, n, s = centre[facing], normal[facing], span[facing]
        v = spread(half)[None, :, None]
        points = (c[:, None] + lift * n[:, None] + half * v * s[:, None])

        # Flat shading: one tone across the face, set by which way it points.
        shade = depth_light(c[:, 2]) * (0.45 + 0.55 * n[:, 2])
        canvas.splat(points, np.repeat(shade[:, None], v.size, axis=1), BAND,
                     np.repeat(along[facing][:, None], v.size, axis=1))


def draw_pairs(canvas):
    """The base pairs: flat rungs from one backbone's centre to the other's."""
    theta = np.linspace(0, 2 * np.pi, PAIRS, endpoint=False)
    a, _, _, _ = strand_frame(theta, 0)
    b, _, _, _ = strand_frame(theta, GROOVE)
    tangent = np.stack([-np.sin(theta), np.cos(theta), np.zeros_like(theta)],
                       axis=-1)

    s = np.linspace(0, 1, int(2 * HELIX_R / SAMPLE) + 1)[None, :, None, None]
    v = spread(PAIR_W)[None, None, :, None]
    points = (a[:, None, None] + (b - a)[:, None, None] * s
              + PAIR_W * v * tangent[:, None, None])

    # Two-toned, so the pairing reads as two halves meeting in the middle.
    tone = np.where(s[..., 0] < 0.5, 1.0, 0.88)
    light = depth_light(points[..., 2]) * tone
    direction = np.broadcast_to((b - a)[:, None, None], points.shape)
    canvas.splat(points, light, PAIR, direction, bias=-3)


def render():
    canvas = Canvas()
    draw_pairs(canvas)
    for offset in (0, GROOVE):
        draw_band(canvas, offset)
    return canvas


def glyph_stamps():
    """Each glyph as an 8x8 boolean mask."""
    return {
        ch: np.array([[bool(row >> (7 - i) & 1) for i in range(8)]
                      for row in rows])
        for ch, rows in GLYPHS.items()
    }


def cells(array):
    """Split a pixel array into (rows, cols, CELL*CELL) character cells."""
    rows, cols = HEIGHT // CELL, WIDTH // CELL
    return (array[:rows * CELL, :cols * CELL]
            .reshape(rows, CELL, cols, CELL)
            .transpose(0, 2, 1, 3)
            .reshape(rows, cols, CELL * CELL))


def render_ascii(canvas, ramp):
    """The scene as characters, one per 8x8 cell, using only ramp colours."""
    kind, light, angle = (cells(a) for a in
                          (canvas.kind, canvas.light, canvas.angle))
    band = (kind == BAND).mean(-1)
    pair = (kind == PAIR).mean(-1)

    chosen = []
    for r, c in zip(*np.nonzero(band + pair >= MIN_INK)):
        # Whichever covers more of the cell decides what the character shows.
        mask = kind[r, c] == (BAND if band[r, c] >= pair[r, c] else PAIR)
        level = light[r, c][mask].mean()

        if band[r, c] >= SOLID:
            glyph = DENSITY[min(int(level * len(DENSITY)), len(DENSITY) - 1)]
        else:
            # Lines have no direction sense, so average on the doubled angle.
            doubled = 2 * angle[r, c][mask]
            line = np.arctan2(np.sin(doubled).mean(), np.cos(doubled).mean()) / 2
            glyph = ORIENTED[round((line % np.pi) / (np.pi / 4)) % 4]
        chosen.append((r, c, glyph, level))

    # Stretched to span the whole ramp: palette.py needs every step present,
    # and which levels actually occur shifts with the band and rung settings.
    low = min(level for *_, level in chosen)
    high = max(level for *_, level in chosen)

    stamps = glyph_stamps()
    out = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    for r, c, glyph, level in chosen:
        tone = ((level - low) / (high - low)) ** ASCII_GAMMA
        step = min(int(tone * len(ramp)), len(ramp) - 1)
        y, x = r * CELL, c * CELL
        out[y:y + CELL, x:x + CELL][stamps[glyph]] = ramp[step]
    return Image.fromarray(out)


def render_source(canvas):
    """The scene as smooth greyscale at full pixel resolution."""
    return Image.fromarray((canvas.light * 255).astype(np.uint8))


def main():
    ramp = [palette.from_hex(c) for c in
            palette.load_palettes()["sources"]["wallpaper"]["ramp"]]
    OUT_SOURCE.parent.mkdir(exist_ok=True)
    canvas = render()
    render_ascii(canvas, ramp).save(OUT_ASCII)
    render_source(canvas).save(OUT_SOURCE)
    print(f"wrote {OUT_ASCII.name} and {OUT_SOURCE.name}")


if __name__ == "__main__":
    main()
