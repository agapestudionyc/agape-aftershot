"""Get photos off a card and onto the drive, provably.

Copies rather than moves, and checksums every file against its original before
calling it done. The card is never touched: until the copy is verified, the
card is still the only copy, and afterwards formatting in-camera is both safer
and better for the card than deleting from a computer.
"""
from __future__ import annotations

import ctypes
import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .scan import JPG_EXTS, RAW_EXTS, SIDECAR_EXTS, VIDEO_EXTS

MEDIA_EXTS = RAW_EXTS | JPG_EXTS | VIDEO_EXTS | SIDECAR_EXTS
COPY_ATTEMPTS = 3
_TYPES = {2: "removable", 3: "fixed", 4: "network", 5: "cd-rom", 6: "ram"}


@dataclass
class Volume:
    letter: str
    label: str = ""
    kind: str = ""
    total: int = 0
    free: int = 0
    dcim: bool = False
    media: int = 0

    @property
    def path(self):
        return Path(self.letter + ":\\")

    @property
    def used(self):
        return max(0, self.total - self.free)


def list_volumes(count_media=False, max_count=20000):
    """Every mounted drive, flagged for whether it looks like a camera card."""
    out = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        return out

    for i in range(26):
        if not (mask >> i) & 1:
            continue
        letter = chr(ord("A") + i)
        root = f"{letter}:\\"
        try:
            kind = _TYPES.get(ctypes.windll.kernel32.GetDriveTypeW(root), "")
        except Exception:
            kind = ""
        if kind in ("cd-rom", "ram", ""):
            continue

        vol = Volume(letter=letter, kind=kind)
        buf = ctypes.create_unicode_buffer(261)
        try:
            ctypes.windll.kernel32.GetVolumeInformationW(
                root, buf, 260, None, None, None, None, 0)
            vol.label = buf.value
        except Exception:
            pass
        try:
            free = ctypes.c_ulonglong(0)
            total = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                root, ctypes.byref(free), ctypes.byref(total), None)
            vol.free, vol.total = free.value, total.value
        except Exception:
            pass

        try:
            vol.dcim = (Path(root) / "DCIM").is_dir()
        except OSError:
            pass
        if count_media:
            vol.media = count_photos(root, cap=max_count)
        out.append(vol)
    return out


def count_photos(root, cap=20000):
    n = 0
    try:
        for p in Path(root).rglob("*"):
            if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                n += 1
                if n >= cap:
                    break
    except (OSError, PermissionError):
        pass
    return n


def find_media(src):
    src = Path(src)
    out = []
    for p in sorted(src.rglob("*")):
        try:
            if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                out.append(p)
        except OSError:
            continue
    return out


def _sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


@dataclass
class ImportResult:
    copied: int = 0
    skipped: int = 0
    failed: list = field(default_factory=list)
    bytes_copied: int = 0
    dest: Path = None
    files: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.failed


def copy_verified(files, dest, flatten=True, verify=True, on_step=None):
    """Copy every file to dest and prove it arrived intact."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    res = ImportResult(dest=dest)

    for i, src in enumerate(files, 1):
        try:
            target = dest / src.name
            if target.exists():
                if target.stat().st_size == src.stat().st_size and (
                        not verify or _sha256(target) == _sha256(src)):
                    res.skipped += 1
                    res.files.append(target)
                    if on_step:
                        on_step(i, len(files), src, "already there")
                    continue
                # Same name, different content - two cards, or DCIM subfolders
                # that both hold a DSC_0001. Never overwrite what is there.
                target = _unique(target)

            tmp = target.with_suffix(target.suffix + ".part")
            # A tired card reader can hand back different bytes on one read
            # and the right ones on the next - that is what DSC_4103 did on
            # 2026-10-20, with Windows logging thousands of retried reads on
            # the reader. One bad read is not a bad file, so copy it again
            # before calling it failed. A file that never matches still fails.
            for attempt in range(1, COPY_ATTEMPTS + 1):
                shutil.copy2(str(src), str(tmp))
                if not verify or _sha256(tmp) == _sha256(src):
                    break
                tmp.unlink(missing_ok=True)
            else:
                res.failed.append((src, f"checksum mismatch after {COPY_ATTEMPTS} tries"
                                        " - the card or the reader is misreading"))
                if on_step:
                    on_step(i, len(files), src, "FAILED verify")
                continue
            if attempt > 1 and on_step:
                on_step(i, len(files), src, f"copied on try {attempt}")
            tmp.replace(target)
            res.copied += 1
            res.bytes_copied += target.stat().st_size
            res.files.append(target)
            if on_step:
                on_step(i, len(files), src, "copied")
        except Exception as exc:
            res.failed.append((src, f"{type(exc).__name__}: {exc}"))
            if on_step:
                on_step(i, len(files), src, "FAILED")
    return res


def _unique(dst: Path) -> Path:
    if not dst.exists():
        return dst
    n = 2
    while True:
        cand = dst.parent / f"{dst.stem} ({n}){dst.suffix}"
        if not cand.exists():
            return cand
        n += 1
