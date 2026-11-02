"""Render a layout template matching the digit sheet's dimensions.

80px white cell, 71px blue gap, repeating across the full 1288x80 canvas.
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).parent.parent
OUT = ROOT / "build" / "template.png"

WIDTH, HEIGHT = 1288, 80
CELL = 80
GAP = 71
WHITE = (255, 255, 255)
BLUE = (0, 0, 255)


def main():
    img = Image.new("RGB", (WIDTH, HEIGHT), BLUE)
    draw = ImageDraw.Draw(img)

    x = 0
    while x < WIDTH:
        draw.rectangle((x, 0, min(x + CELL, WIDTH) - 1, HEIGHT - 1), fill=WHITE)
        x += CELL + GAP

    OUT.parent.mkdir(exist_ok=True)
    img.save(OUT)
    print(f"wrote {OUT} ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    main()
