"""Pull the embedded JPEG preview out of a RAW file.

Every RAW format on the market stores one or more full JPEGs inside the file so
cameras and viewers can show the shot without demosaicing it. Rather than write
a parser per format (NEF's big preview hides in the MakerNote, CR3 is ISO-BMFF,
RAF has its own header), this finds every embedded JPEG by signature and keeps
the largest. That works uniformly across NEF/CR2/CR3/ARW/DNG/RAF/ORF/RW2 with
no vendor libraries and no external binaries.
"""
from __future__ import annotations

import io
import mmap
import os
import struct
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFile, ImageOps

from .scan import JPG_EXTS

ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = None

SOI = b"\xff\xd8\xff"
# Byte following the SOI marker in a real JPEG: an APPn segment, a quantisation
# table, a frame header or a comment. Filters out random sensor data that
# happens to contain the three-byte signature.
_GOOD_NEXT = {0xE0, 0xE1, 0xE2, 0xE3, 0xE8, 0xEC, 0xED, 0xEE, 0xDB, 0xC0, 0xC4, 0xFE}
_HEADER_PROBE = 1 << 20  # enough to reach SOF past a fat EXIF block
_MAX_CANDIDATES = 400
_MAX_PREVIEW_BYTES = 64 << 20
_MIN_PIXELS = 64 * 64

# EXIF orientation value -> sequence of PIL transposes
_ORIENT_OPS = {
    2: [Image.FLIP_LEFT_RIGHT],
    3: [Image.ROTATE_180],
    4: [Image.FLIP_TOP_BOTTOM],
    5: [Image.FLIP_LEFT_RIGHT, Image.ROTATE_90],
    6: [Image.ROTATE_270],
    7: [Image.FLIP_LEFT_RIGHT, Image.ROTATE_270],
    8: [Image.ROTATE_90],
}


@dataclass
class Preview:
    source: Path
    out: Path | None
    embedded: tuple | None = None  # preview resolution as stored in the RAW
    saved: tuple | None = None     # resolution after downscaling
    orientation: int | None = None
    method: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.out is not None and not self.error


def _raw_orientation(mm):
    """Read Orientation (tag 0x0112) from IFD0 of a TIFF-structured RAW.

    Many embedded previews carry no EXIF of their own, so a portrait frame comes
    out sideways without this. Sideways faces are faces the detector misses.
    """
    try:
        bo = mm[:2]
        if bo == b"II":
            e = "<"
        elif bo == b"MM":
            e = ">"
        else:
            return None
        offset = struct.unpack(e + "I", mm[4:8])[0]
        if not 8 <= offset < len(mm) - 2:
            return None
        count = struct.unpack(e + "H", mm[offset:offset + 2])[0]
        if count > 512:
            return None
        for k in range(count):
            p = offset + 2 + k * 12
            if p + 12 > len(mm):
                break
            tag = struct.unpack(e + "H", mm[p:p + 2])[0]
            if tag == 0x0112:
                val = struct.unpack(e + "H", mm[p + 8:p + 10])[0]
                return val if 1 <= val <= 8 else None
    except Exception:
        return None
    return None


def _candidate_offsets(mm, size):
    found = []
    pos = 0
    while len(found) < _MAX_CANDIDATES:
        i = mm.find(SOI, pos)
        if i < 0:
            break
        if i + 3 < size and mm[i + 3] in _GOOD_NEXT:
            found.append(i)
        pos = i + 2
    return found


def _largest_embedded(mm, size):
    """-> (byte offset, (w, h)) of the biggest embedded JPEG, or None."""
    best = None
    for off in _candidate_offsets(mm, size):
        try:
            probe = mm[off:min(size, off + _HEADER_PROBE)]
            if len(probe) < 512:
                continue
            with Image.open(io.BytesIO(probe)) as im:
                if im.format != "JPEG":
                    continue
                w, h = im.size
        except Exception:
            continue
        area = w * h
        if area < _MIN_PIXELS:
            continue
        if best is None or area > best[0]:
            best = (area, off, (w, h))
    if best is None:
        return None
    return best[1], best[2]


def _apply_orientation(im, raw_orient):
    """Prefer the preview's own EXIF orientation, fall back to the RAW's."""
    used = None
    try:
        own = im.getexif().get(0x0112)
    except Exception:
        own = None
    if own and 1 <= int(own) <= 8:
        im = ImageOps.exif_transpose(im)
        used = int(own)
    elif raw_orient and raw_orient in _ORIENT_OPS:
        for op in _ORIENT_OPS[raw_orient]:
            im = im.transpose(op)
        used = raw_orient
    return im, used


def extract(source, out_path=None, long_side=2048, quality=90, overwrite=False):
    """Extract the largest embedded JPEG from `source` and write it to `out_path`."""
    source = Path(source)
    out_path = Path(out_path) if out_path else None

    if out_path and out_path.exists() and not overwrite:
        try:
            with Image.open(out_path) as im:
                return Preview(source, out_path, None, im.size, None, "cached")
        except Exception:
            pass  # corrupt cache entry, fall through and regenerate

    try:
        size = source.stat().st_size
        if size == 0:
            return Preview(source, None, error="empty file")

        is_jpeg_like = source.suffix.lower() in JPG_EXTS
        raw_orient = None
        embedded = None

        with open(source, "rb") as fh:
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                if is_jpeg_like:
                    offset, method = 0, "direct"
                else:
                    hit = _largest_embedded(mm, size)
                    if hit is None:
                        return Preview(
                            source, None,
                            error="no embedded JPEG preview found in this file",
                        )
                    offset, embedded = hit
                    method = "embedded"
                    raw_orient = _raw_orientation(mm)
                buf = mm[offset:min(size, offset + _MAX_PREVIEW_BYTES)]

        with Image.open(io.BytesIO(buf)) as im:
            im.load()
            if embedded is None:
                embedded = im.size
            im, used = _apply_orientation(im, raw_orient)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            if long_side and max(im.size) > long_side:
                im.thumbnail((long_side, long_side), Image.LANCZOS)
            saved = im.size
            if out_path:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = out_path.with_suffix(out_path.suffix + ".part")
                im.save(tmp, "JPEG", quality=quality, optimize=True)
                tmp.replace(out_path)

        return Preview(source, out_path, embedded, saved, used, method)
    except Exception as exc:
        return Preview(source, None, error=f"{type(exc).__name__}: {exc}")


def full_jpeg(source, quality=95):
    """The biggest JPEG a photo holds, at full size, upright, as bytes.

    This is what the zoom reads when you are judging focus: the cached preview
    is cut down to 2048 px, a third of a 24 MP frame, which hides exactly the
    softness you are looking for. Nothing is written to disk. When the embedded
    JPEG is already upright it goes out byte for byte, with no re-encode.
    """
    source = Path(source)
    size = source.stat().st_size
    with open(source, "rb") as fh:
        with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            if source.suffix.lower() in (".jpg", ".jpeg", ".jpe"):
                return bytes(mm[:size])
            hit = _largest_embedded(mm, size)
            if hit is None:
                raise ValueError("no embedded JPEG in this file")
            raw_orient = _raw_orientation(mm)
            buf = bytes(mm[hit[0]:min(size, hit[0] + _MAX_PREVIEW_BYTES)])
    with Image.open(io.BytesIO(buf)) as im:
        try:
            own = im.getexif().get(0x0112)
        except Exception:
            own = None
        if not own and raw_orient in (None, 1):
            return buf
        im.load()
        im, _ = _apply_orientation(im, raw_orient)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, "JPEG", quality=quality, subsampling=0)
        return out.getvalue()


def cache_dir_for(root):
    return Path(root) / "_cullkit" / "previews"


def extract_many(sources, cache_dir, long_side=2048, workers=0,
                 overwrite=False, on_done=None):
    """Extract previews for many files. Threaded: Pillow releases the GIL."""
    sources = [Path(s) for s in sources]
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    workers = workers or min(8, (os.cpu_count() or 4))

    from .stop import checkpoint

    def job(src):
        # Before each file, not after: a stop pressed mid-batch lets the files
        # already under way finish and skips every one still queued.
        checkpoint()
        out = cache_dir / (src.stem + ".jpg")
        res = extract(src, out, long_side=long_side, overwrite=overwrite)
        if on_done:
            on_done(res)
        return res

    if workers <= 1:
        return [job(s) for s in sources]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(job, sources))
