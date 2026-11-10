"""Read capture time and camera settings straight out of a RAW file.

Only what shoot detection actually needs, parsed from the TIFF/Exif structure
that NEF, CR2, ARW, DNG, ORF and RW2 all share. Formats that are not TIFF
containers (notably CR3, which is ISO-BMFF) fall back to the file's modified
time, which is close enough to cluster by since cameras write files as they
are shot.
"""
from __future__ import annotations

import mmap
import struct
from datetime import datetime
from pathlib import Path

# IFD0
_MAKE, _MODEL, _DATETIME, _EXIF_PTR = 0x010F, 0x0110, 0x0132, 0x8769
# Exif IFD
_DT_ORIG, _DT_DIGI = 0x9003, 0x9004
_EXPOSURE, _FNUMBER, _ISO, _FOCAL, _LENS = 0x829A, 0x829D, 0x8827, 0x920A, 0xA434

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


def _read_ifd(mm, e, offset, want, out, base=0):
    """Pull the wanted tags out of one IFD. Returns the next-IFD offset."""
    try:
        count = struct.unpack(e + "H", mm[offset:offset + 2])[0]
    except Exception:
        return 0
    if count > 1024:
        return 0
    for k in range(count):
        p = offset + 2 + k * 12
        if p + 12 > len(mm):
            break
        try:
            tag, typ, n = struct.unpack(e + "HHI", mm[p:p + 8])
        except Exception:
            break
        if tag not in want:
            continue
        size = _TYPE_SIZE.get(typ, 0) * n
        if size == 0:
            continue
        if size <= 4:
            raw = mm[p + 8:p + 8 + size]
        else:
            try:
                off = struct.unpack(e + "I", mm[p + 8:p + 12])[0] + base
            except Exception:
                continue
            if not 0 <= off < len(mm) - size:
                continue
            raw = mm[off:off + size]
        out[tag] = _decode(e, typ, n, raw)
    try:
        return struct.unpack(e + "I", mm[offset + 2 + count * 12:offset + 6 + count * 12])[0]
    except Exception:
        return 0


def _decode(e, typ, n, raw):
    try:
        if typ == 2:
            return raw.split(b"\x00")[0].decode("ascii", "replace").strip()
        if typ == 3:
            v = struct.unpack(e + "H" * n, raw[: 2 * n])
        elif typ in (4, 9):
            v = struct.unpack(e + ("I" if typ == 4 else "i") * n, raw[: 4 * n])
        elif typ in (5, 10):
            f = e + ("II" if typ == 5 else "ii") * n
            nums = struct.unpack(f, raw[: 8 * n])
            v = tuple(
                (nums[i] / nums[i + 1]) if nums[i + 1] else 0.0
                for i in range(0, len(nums), 2)
            )
        else:
            return raw
        return v[0] if n == 1 else v
    except Exception:
        return None


def _parse_dt(s):
    if not isinstance(s, str):
        return None
    s = s.strip().rstrip("\x00")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def read_exif(path) -> dict:
    """-> dict with taken, make, model, lens, iso, fnumber, exposure, focal.

    `taken` is always present: EXIF capture time when available, otherwise the
    file's modified time, with `taken_from` saying which.
    """
    path = Path(path)
    info: dict = {}
    try:
        with open(path, "rb") as fh:
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                bo = mm[:2]
                if bo == b"II":
                    e = "<"
                elif bo == b"MM":
                    e = ">"
                else:
                    e = None

                if e:
                    first = struct.unpack(e + "I", mm[4:8])[0]
                    tags: dict = {}
                    if 8 <= first < len(mm):
                        _read_ifd(mm, e, first,
                                  {_MAKE, _MODEL, _DATETIME, _EXIF_PTR}, tags)
                    exif_off = tags.get(_EXIF_PTR)
                    if isinstance(exif_off, int) and 8 <= exif_off < len(mm):
                        _read_ifd(mm, e, exif_off,
                                  {_DT_ORIG, _DT_DIGI, _EXPOSURE, _FNUMBER,
                                   _ISO, _FOCAL, _LENS}, tags)

                    info["make"] = tags.get(_MAKE)
                    info["model"] = tags.get(_MODEL)
                    info["lens"] = tags.get(_LENS)
                    info["iso"] = tags.get(_ISO)
                    info["fnumber"] = tags.get(_FNUMBER)
                    info["exposure"] = tags.get(_EXPOSURE)
                    info["focal"] = tags.get(_FOCAL)
                    dt = (_parse_dt(tags.get(_DT_ORIG))
                          or _parse_dt(tags.get(_DT_DIGI))
                          or _parse_dt(tags.get(_DATETIME)))
                    if dt:
                        info["taken"] = dt
                        info["taken_from"] = "exif"
    except Exception:
        pass

    if "taken" not in info:
        try:
            info["taken"] = datetime.fromtimestamp(path.stat().st_mtime)
            info["taken_from"] = "file time"
        except OSError:
            info["taken"] = None
            info["taken_from"] = "unknown"
    return info


def camera_label(info) -> str:
    make = (info.get("make") or "").split()[0] if info.get("make") else ""
    model = info.get("model") or ""
    if model.lower().startswith(make.lower()) and make:
        return model
    return (make + " " + model).strip() or "unknown camera"
