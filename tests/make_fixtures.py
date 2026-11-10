"""Build fake RAW files that are structured like real ones, for testing.

A real NEF/CR2/ARW is a TIFF container with full JPEGs buried inside it among
megabytes of sensor data. These fixtures reproduce exactly that shape: TIFF
header, an Orientation tag, random "sensor" bytes, a small thumbnail JPEG and a
larger preview JPEG. If the extractor finds the big one here, it will find it
in the real thing.
"""
import random
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def make_jpeg(w, h, seed, text):
    rnd = random.Random(seed)
    im = Image.new("RGB", (w, h), (rnd.randint(30, 90),) * 3)
    d = ImageDraw.Draw(im)
    for _ in range(14):
        x0, y0 = rnd.randint(0, w), rnd.randint(0, h)
        d.ellipse([x0, y0, x0 + rnd.randint(20, w // 3), y0 + rnd.randint(20, h // 3)],
                  fill=(rnd.randint(60, 255), rnd.randint(60, 255), rnd.randint(60, 255)))
    d.text((10, 10), text, fill=(255, 255, 255))
    import io
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def tiff_header(orientation):
    """IFD0 with a single Orientation tag, laid out like a real RAW."""
    out = bytearray(b"II*\x00")
    out += struct.pack("<I", 8)          # IFD0 at byte 8
    out += struct.pack("<H", 1)          # one entry
    out += struct.pack("<HHI", 0x0112, 3, 1) + struct.pack("<HH", orientation, 0)
    out += struct.pack("<I", 0)          # no next IFD
    return bytes(out)


def make_raw(path, frame, orientation=1, thumb=(160, 120), preview=(1600, 1067),
             sensor_kb=400):
    rnd = random.Random(frame)
    blob = bytearray(tiff_header(orientation))
    blob += make_jpeg(*thumb, frame, f"thumb {frame}")
    # Sensor noise, which will contain stray ff d8 ff sequences - the point.
    blob += bytes(rnd.getrandbits(8) for _ in range(sensor_kb * 1024))
    blob += make_jpeg(*preview, frame, f"PREVIEW {frame}")
    blob += bytes(rnd.getrandbits(8) for _ in range(64 * 1024))
    Path(path).write_bytes(blob)


def build(root):
    root = Path(root)
    shoot = root / "shoot"
    shoot.mkdir(parents=True, exist_ok=True)
    for n in range(1200, 1230):
        make_raw(shoot / f"DSC_{n}.NEF", n, orientation=6 if n % 5 == 0 else 1)
        if n % 3 == 0:
            (shoot / f"DSC_{n}.xmp").write_text("<x:xmpmeta/>", encoding="utf-8")
        if n % 7 == 0:
            (shoot / f"DSC_{n}.JPG").write_bytes(make_jpeg(800, 533, n, f"jpg {n}"))
    # A second body, same frame numbers -> the ambiguity case.
    for n in (1205, 1206):
        make_raw(shoot / f"IMG_{n}.CR2", n + 9000)
    # Lightroom-style doubled sidecar name.
    (shoot / "DSC_1201.NEF.xmp").write_text("<x:xmpmeta/>", encoding="utf-8")

    (root / "picks.txt").write_text(
        "Selects - boda 2026-03-14\n"
        "1200, 1203-1207\n"
        "DSC_1210.NEF\n"
        "1215 a 1218\n"
        "0001221\n"
        "1999\n"          # deliberately missing
        "notas: revisar\n",
        encoding="utf-8",
    )
    print(f"fixtures in {shoot}")
    return shoot


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else ".")
