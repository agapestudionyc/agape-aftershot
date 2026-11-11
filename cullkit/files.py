"""The file explorer: everything the Files screen asks the computer to do.

Kept out of server.py on purpose. That file is the session - who holds the
controls, what is running - and this is a different thing: looking at drives
and moving files about, which has nothing to say about any of that.

Three rules, all of them the program's own and none of them new:

- **Nothing is deleted.** Delete here means the Recycle Bin, where Windows
  keeps it until *you* empty it. There is no code path in this module that
  unlinks a file or removes a folder that has anything in it.
- **Nothing is overwritten.** A name already taken at the destination is
  reported and skipped (a move) or given a number (a copy, an upload).
- **Not while a job is running.** Moving a folder out from under a Sift is how
  a shoot comes back unreadable, so every action that changes the disk is
  refused while anything is executing. Looking is always allowed.
"""
from __future__ import annotations

import ctypes
import io
import mimetypes
import os
import shutil
import subprocess
import time
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff",
              ".psd", ".ico", ".tga", ".jfif", ".avif"}
RAW_EXTS = {".nef", ".cr2", ".cr3", ".arw", ".raf", ".dng", ".orf", ".rw2", ".pef", ".srw"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv", ".flv",
              ".mts", ".m2ts", ".3gp", ".mpg", ".mpeg", ".ts", ".ogv"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
              ".aiff", ".aif", ".amr", ".mka", ".ape", ".ac3"}
TEXT_EXTS = {".txt", ".md", ".json", ".csv", ".log", ".xmp", ".xml", ".py", ".js",
             ".css", ".html", ".htm", ".ini", ".yml", ".yaml", ".cmd", ".bat", ".ps1",
             ".toml", ".cfg", ".srt", ".vtt", ".sh", ".ts", ".tsx", ".jsx", ".c", ".h",
             ".cpp", ".java", ".go", ".rs", ".sql", ".tex", ".rtf", ".nfo", ".url",
             ".gitignore", ".env", ".conf", ".lrtemplate", ".pp3", ".cube", ".lut"}
BAD_NAME = set('\\/:*?"<>|')


def kind_of(ext: str) -> str:
    ext = ext.lower()
    if ext in IMAGE_EXTS or ext == ".svg":
        return "image"
    if ext in RAW_EXTS:
        return "raw"
    if ext in VIDEO_EXTS and ext != ".ts":      # .ts is TypeScript far more often
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    return "file"


def _hidden(p: Path, st=None) -> bool:
    if p.name.startswith((".", "$")):
        return True
    try:
        st = st or p.stat()
        return bool(getattr(st, "st_file_attributes", 0) & 0x2      # Windows: hidden
                    or getattr(st, "st_flags", 0) & 0x8000)         # macOS: UF_HIDDEN
    except OSError:
        return False


def _row(p: Path) -> dict:
    try:
        st = p.stat()
    except OSError:
        return {"name": p.name, "path": str(p), "dir": p.is_dir(), "size": 0,
                "mtime": 0, "ext": p.suffix.lower(), "kind": "file",
                "hidden": False, "broken": True}
    is_dir = p.is_dir()
    ext = "" if is_dir else p.suffix.lower()
    return {"name": p.name, "path": str(p), "dir": is_dir,
            "size": 0 if is_dir else st.st_size, "mtime": st.st_mtime,
            "ext": ext, "kind": "folder" if is_dir else kind_of(ext),
            "hidden": _hidden(p, st)}


def places(config: dict) -> list:
    """The folders worth one tap. The program's own two first - Archive, then
    Working - because those are where his photographs are; the rest after."""
    home = Path.home()
    out = []
    for label, key in (("Archive", "archive_root"), ("Working", "work_root")):
        v = str(config.get(key) or "")
        if v and Path(v).is_dir():
            out.append({"label": label, "path": v, "kind": "aftershot"})
    for label, p in (("Desktop", home / "Desktop"),
                     ("Desktop", home / "OneDrive" / "Escritorio"),
                     ("Downloads", home / "Downloads"),
                     ("Pictures", home / "Pictures"),
                     ("Movies", home / "Movies"),
                     ("Documents", home / "Documents")):
        if p.is_dir() and not any(o["label"] == label for o in out):
            out.append({"label": label, "path": str(p), "kind": "home"})
    return out


def listing(path: str) -> dict:
    root = Path(path)
    if not root.is_dir():
        return {"error": f"not a folder: {path}"}
    rows = []
    try:
        with os.scandir(root) as it:
            for e in it:
                rows.append(_row(Path(e.path)))
    except PermissionError:
        return {"error": "Windows will not let this folder be opened"}
    except OSError as exc:
        return {"error": str(exc)}
    parent = str(root.parent) if root.parent != root else ""
    # `/Volumes/Photos` has a parent folder, but it is the top of a drive:
    # one step up from it is the list of drives, as it is from `E:\\`.
    from . import plat
    if not plat.IS_WIN and str(root) == plat.mount_of(root):
        parent = ""
    return {"path": str(root), "parent": parent, "name": root.name or str(root),
            "items": rows}


def search(path: str, q: str, limit=400, seconds=6.0) -> dict:
    """Names containing `q`, anywhere under `path`. Bounded in both count and
    time: an external drive can hold a million files, and a search that never
    answers is worse than one that says it stopped early."""
    root = Path(path)
    q = q.strip().lower()
    if not root.is_dir() or not q:
        return {"items": [], "more": False}
    out, t0, more = [], time.time(), False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(("$", ".")) and d != "_cullkit"]
        for name in dirnames + filenames:
            if q in name.lower():
                out.append(_row(Path(dirpath) / name))
                if len(out) >= limit:
                    more = True
                    break
        if more or time.time() - t0 > seconds:
            more = True
            break
    return {"items": out, "more": more}


def info(path: str, seconds=4.0) -> dict:
    p = Path(path)
    if not p.exists():
        return {"error": "that is no longer there"}
    row = _row(p)
    try:
        row["created"] = p.stat().st_ctime
    except OSError:
        pass
    if p.is_dir():
        total = files = folders = 0
        t0, partial = time.time(), False
        for dirpath, dirnames, filenames in os.walk(p):
            folders += len(dirnames)
            for f in filenames:
                files += 1
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
            if time.time() - t0 > seconds:
                partial = True
                break
        row.update(size=total, files=files, folders=folders, partial=partial)
    elif row["kind"] in ("image", "raw"):
        try:
            from . import exif
            e = exif.read_exif(p) or {}
            row["exif"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                           for k, v in e.items()
                           if isinstance(v, (str, int, float)) or hasattr(v, "isoformat")}
        except Exception:
            pass
        try:
            from PIL import Image
            if row["kind"] == "image":
                with Image.open(p) as im:
                    row["pixels"] = list(im.size)
        except Exception:
            pass
    return row


def _raw_picture(p: Path, size: int):
    """The camera's own JPEG out of a RAW, decoded small.

    `previews.full_jpeg` is the wrong tool for a thumbnail: on a frame shot
    upright it decodes all twenty megapixels and encodes them again, which
    measured 1.8 seconds a file on the real card - a folder of 454 frames
    never finished drawing. A JPEG can be decoded straight to an eighth of its
    size (`draft`), and turning a small picture round costs nothing: 0.08s.
    """
    import mmap

    from PIL import Image

    from . import previews as P

    total = p.stat().st_size
    want = max(size, 480)
    with open(p, "rb") as fh:
        with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            orient = P._raw_orientation(mm)
            # Look only at the head of the file first. Searching all thirty
            # megabytes for JPEG markers means reading all thirty off the
            # card, which was most of the second each frame took; the camera
            # keeps its previews in the first few. Of those, take the
            # smallest that is still big enough - not the largest.
            head, pos, found = min(total, 3 << 20), 0, []
            while len(found) < 60:
                i = mm.find(P.SOI, pos, head)
                if i < 0:
                    break
                if i + 3 < total and mm[i + 3] in P._GOOD_NEXT:
                    try:
                        with Image.open(io.BytesIO(mm[i:min(total, i + (256 << 10))])) as probe:
                            if probe.format == "JPEG":
                                found.append((max(probe.size), i))
                    except Exception:
                        pass
                pos = i + 2
            enough = sorted(x for x in found if x[0] >= want)
            if enough:
                long_side, off = enough[0]
            else:
                hit = P._largest_embedded(mm, total)
                if hit is None:
                    return None
                off, long_side = hit[0], max(hit[1])
            cap = (3 << 20) if long_side <= 2200 else P._MAX_PREVIEW_BYTES
            buf = bytes(mm[off:min(total, off + cap)])
    im = Image.open(io.BytesIO(buf))
    im.draft("RGB", (size, size))
    im.load()
    im, _ = P._apply_orientation(im, orient)
    return im


# Thumbnails made this session, newest last. A folder is looked at more than
# once - scrolled, left, come back to - and a card reader is slow.
_THUMBS: dict = {}
_THUMBS_MAX = 600


def thumb(path: str, size: int = 320) -> bytes | None:
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size, int(size))
    except OSError:
        return None
    hit = _THUMBS.get(key)
    if hit is not None:
        return hit
    got = _thumb(path, size)
    if got is not None:
        if len(_THUMBS) >= _THUMBS_MAX:
            for k in list(_THUMBS)[:_THUMBS_MAX // 4]:
                _THUMBS.pop(k, None)
        _THUMBS[key] = got
    return got


def _thumb(path: str, size: int = 320) -> bytes | None:
    """A picture of the file, or None when there is not one to be had.

    A RAW gives up the JPEG its camera buried in it - read, never written: this
    is a glance, and a glance must not leave a cache behind on a card.
    """
    from PIL import Image, ImageOps

    p = Path(path)
    ext = p.suffix.lower()
    size = max(64, min(int(size), 4096))
    try:
        if ext in RAW_EXTS:
            cached = p.parent / "_cullkit" / "previews" / f"{p.stem}.jpg"
            if cached.is_file():
                im = Image.open(cached)
            else:
                im = _raw_picture(p, size)
                if im is None:
                    return None
        elif ext in IMAGE_EXTS:
            im = Image.open(p)
        elif ext in VIDEO_EXTS and ext != ".ts":
            frame = video_frame(p)
            if not frame:
                return None
            im = Image.open(io.BytesIO(frame))
        else:
            return None
        with im:
            if ext not in RAW_EXTS:
                try:
                    im.draft("RGB", (size, size))     # a no-op for anything but JPEG
                except Exception:
                    pass
                im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            im.thumbnail((size, size), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=88 if size >= 700 else 78, optimize=True)
            return buf.getvalue()
    except Exception:
        return None


def video_frame(p: Path) -> bytes | None:
    """One frame from a second in, as a JPEG. None without ffmpeg."""
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    try:
        run = subprocess.run(
            [ff, "-v", "error", "-ss", "1", "-i", str(p), "-frames:v", "1",
             "-vf", "scale=640:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            capture_output=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return run.stdout or None
    except Exception:
        return None


def transcoder(p: Path, start: float = 0.0):
    """A running ffmpeg turning this file into something a browser plays.

    For what the browser cannot open by itself - an .mkv, an .avi, a phone's
    HEVC .mov, a .wma. Nothing is written to disk: it is converted as it is
    watched and thrown away. Returns (process, content type) or (None, why).
    """
    ff = shutil.which("ffmpeg")
    if not ff:
        return None, "ffmpeg is not installed, so this format cannot be played here"
    ext = p.suffix.lower()
    seek = ["-ss", f"{max(0.0, start):.2f}"] if start > 0 else []
    if ext in AUDIO_EXTS:
        cmd = [ff, "-v", "error", *seek, "-i", str(p), "-vn",
               "-c:a", "libmp3lame", "-q:a", "4", "-f", "mp3", "pipe:1"]
        ctype = "audio/mpeg"
    else:
        cmd = [ff, "-v", "error", *seek, "-i", str(p),
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
               "-vf", "scale='min(1280,iw)':-2", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "128k", "-ac", "2",
               "-movflags", "frag_keyframe+empty_moov+default_base_moof",
               "-f", "mp4", "pipe:1"]
        ctype = "video/mp4"
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        return None, str(exc)
    return proc, ctype


def content_type(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in TEXT_EXTS:
        return "text/plain; charset=utf-8"
    return mimetypes.guess_type(str(p))[0] or "application/octet-stream"


# ------------------------------------------------------------------ changing
def _valid_name(name: str) -> bool:
    return bool(name) and name not in (".", "..") and not (BAD_NAME & set(name)) \
        and not name.endswith((" ", "."))


def _protected(p: Path) -> str:
    """Why this path may not be moved, renamed or binned - or '' if it may."""
    try:
        r = p.resolve()
    except OSError:
        r = p
    from . import plat

    if r.parent == r or str(r) == plat.mount_of(r):
        return "that is a whole drive"
    for guard in plat.protected_roots() + [Path.home() / ".cullkit"]:
        try:
            g = guard.resolve()
        except OSError:
            continue
        if r == g or g in r.parents:
            return "that belongs to the system or to this program"
    if r == Path.home().resolve():
        return "that is your whole user folder"
    return ""


def _unique(dst: Path) -> Path:
    if not dst.exists():
        return dst
    stem, suf = dst.stem, dst.suffix
    for n in range(2, 10000):
        cand = dst.with_name(f"{stem} ({n}){suf}")
        if not cand.exists():
            return cand
    raise OSError("could not find a free name")


def mkdir(parent: str, name: str) -> dict:
    name = (name or "").strip()
    root = Path(parent)
    if not root.is_dir():
        return {"error": "that folder is no longer there"}
    if not _valid_name(name):
        return {"error": "not a valid name"}
    new = root / name
    if new.exists():
        return {"error": f"“{name}” is already taken here"}
    try:
        new.mkdir()
    except OSError as exc:
        return {"error": str(exc)}
    return {"ok": True, "path": str(new)}


def copy(paths: list, dest: str) -> dict:
    """Copy into `dest`. A name already there gets a number; nothing is
    replaced. Copying a folder into itself is refused."""
    d = Path(dest)
    if not d.is_dir():
        return {"error": "destination is not a folder"}
    done, skipped = [], []
    for s in paths:
        src = Path(s)
        if not src.exists():
            skipped.append({"name": src.name, "why": "no longer there"})
            continue
        try:
            sr, dr = src.resolve(), d.resolve()
        except OSError:
            sr, dr = src, d
        if src.is_dir() and (sr == dr or sr in dr.parents):
            skipped.append({"name": src.name, "why": "can't copy a folder into itself"})
            continue
        try:
            target = _unique(d / src.name)
            if src.is_dir():
                shutil.copytree(src, target)
            else:
                shutil.copy2(src, target)
            done.append({"from": str(src), "to": str(target)})
        except OSError as exc:
            skipped.append({"name": src.name, "why": str(exc)})
    return {"ok": True, "copied": done, "skipped": skipped}


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p), ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]


def trash(paths: list) -> dict:
    """To the Recycle Bin - and only there.

    FOF_ALLOWUNDO is what makes it the bin rather than gone. If Windows cannot
    bin something (a network share, a drive with no bin) the call fails and
    the file stays exactly where it was; this never falls back to deleting.
    """
    done, skipped = [], []
    if os.name != "nt":
        # A Mac: the Trash, through Finder so it can be put back. See plat.trash.
        from . import plat
        for s in paths:
            p = Path(s)
            why = _protected(p)
            if why:
                skipped.append({"name": p.name or str(p), "why": why})
            elif not p.exists():
                skipped.append({"name": p.name, "why": "no longer there"})
            elif plat.trash(p):
                done.append(str(p))
            else:
                skipped.append({"name": p.name, "why": "it could not be put in the Trash"})
        return {"ok": True, "trashed": done, "skipped": skipped}
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = \
        3, 0x4, 0x10, 0x40, 0x400
    FOF_WANTNUKEWARNING = 0x4000     # fail rather than delete what cannot be binned
    for s in paths:
        p = Path(s)
        why = _protected(p)
        if why:
            skipped.append({"name": p.name or str(p), "why": why})
            continue
        if not p.exists():
            skipped.append({"name": p.name, "why": "no longer there"})
            continue
        op = _SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        op.pFrom = str(p) + "\0"           # ctypes adds the second terminator
        op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT |
                     FOF_NOERRORUI | FOF_WANTNUKEWARNING)
        try:
            rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        except Exception as exc:           # not Windows, or shell32 missing
            skipped.append({"name": p.name, "why": f"no Recycle Bin here ({exc})"})
            continue
        if rc == 0 and not op.fAnyOperationsAborted and not p.exists():
            done.append(str(p))
        else:
            skipped.append({"name": p.name,
                            "why": "Windows could not put it in the Recycle Bin"})
    return {"ok": True, "trashed": done, "skipped": skipped}


def check_protected(paths: list) -> str:
    for s in paths:
        why = _protected(Path(s))
        if why:
            return f"{Path(s).name or s}: {why}"
    return ""


def save_upload(dest: str, name: str, stream, length: int) -> dict:
    """One file from a phone or another computer, written beside the others.

    To a `.part` name first and renamed when every byte is in, so a dropped
    connection never leaves something that looks like a finished file.
    """
    d = Path(dest)
    name = Path(name).name.strip()
    if not d.is_dir():
        return {"error": "that folder is no longer there"}
    if not _valid_name(name):
        return {"error": "not a valid file name"}
    try:
        if shutil.disk_usage(d).free < length + (64 << 20):
            return {"error": "not enough room on that drive"}
    except OSError:
        pass
    target = _unique(d / name)
    part = target.with_name(target.name + ".part")
    left = length
    try:
        with open(part, "wb") as out:
            while left > 0:
                chunk = stream.read(min(left, 1 << 20))
                if not chunk:
                    break
                out.write(chunk)
                left -= len(chunk)
        if left:
            part.unlink(missing_ok=True)
            return {"error": "the upload was cut short"}
        part.rename(target)
    except OSError as exc:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        return {"error": str(exc)}
    return {"ok": True, "path": str(target), "name": target.name}


def open_on_pc(path: str, reveal=False) -> dict:
    """Open with whatever Windows opens it with, on this computer's screen."""
    p = Path(path)
    if not p.exists():
        return {"error": "that is no longer there"}
    try:
        from . import plat
        plat.open_path(str(p), reveal=reveal)
    except (OSError, AttributeError) as exc:
        return {"error": str(exc)}
    return {"ok": True}
