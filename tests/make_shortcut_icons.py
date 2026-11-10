"""Draws the two home-screen shortcut icons: a plus in a disc.

Green is Aftershot, red is the Darkroom - the same plus as the New Task button
in the app, so the icon on the phone and the button it stands for are one mark.

Run it again only if the colours change:

    %USERPROFILE%\\.cullkit\\venv\\Scripts\\python.exe tests\\make_shortcut_icons.py

Drawn at four times the size and scaled down, because Pillow's own ellipse has
no anti-aliasing and a disc with a stair-stepped edge is the first thing an eye
catches on a home screen.
"""
from pathlib import Path

from PIL import Image, ImageDraw

WEB = Path(__file__).resolve().parent.parent / "cullkit" / "web"
INK = (13, 13, 13, 255)                 # --paper in the dark theme
COLOURS = {
    "aftershot": (61, 255, 135, 255),   # --accent
    "darkroom": (255, 82, 64, 255),     # the safelight
    "files": (90, 184, 255, 255),       # --files, the third room
}
SIZES = (96, 192, 512)


def mark(d, big, name, reach):
    """The plus for the two rooms; a folder for Files. `reach` is half the
    mark's width as a fraction of the icon."""
    c = big / 2
    if name == "files":
        w, h = big * reach, big * reach * 0.74
        r = big * 0.035
        d.rounded_rectangle((c - w, c - h * 0.72, c - w * 0.08, c - h * 0.2), radius=r, fill=INK)
        d.rounded_rectangle((c - w, c - h * 0.42, c + w, c + h), radius=r * 1.5, fill=INK)
        return
    arm, thick = big * reach, big * reach * 0.33
    r = thick / 2
    d.rounded_rectangle((c - arm, c - r, c + arm, c + r), radius=r, fill=INK)
    d.rounded_rectangle((c - r, c - arm, c + r, c + arm), radius=r, fill=INK)


def draw(colour, size, name=""):
    big = size * 4
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, big - 1, big - 1), fill=colour)
    mark(d, big, name, 0.26)
    return im.resize((size, size), Image.LANCZOS)


def draw_full(colour, size, name=""):
    """The same mark for a launcher that cuts its own shape.

    A plain icon is set small inside a white disc by Android. A *maskable* one
    is allowed to fill the disc, so this is colour to every edge with the plus
    kept inside the middle 60% - the part no launcher shape ever crops.
    """
    big = size * 4
    im = Image.new("RGBA", (big, big), colour)
    mark(ImageDraw.Draw(im), big, name, 0.18)
    return im.resize((size, size), Image.LANCZOS)


def main():
    for name, colour in COLOURS.items():
        for size in SIZES:
            out = WEB / f"shortcut-{name}-{size}.png"
            draw(colour, size, name).save(out, optimize=True)
            print(out.name, out.stat().st_size)
        for size in (192, 512):
            out = WEB / f"shortcut-{name}-maskable-{size}.png"
            draw_full(colour, size, name).save(out, optimize=True)
            print(out.name, out.stat().st_size)


if __name__ == "__main__":
    main()
