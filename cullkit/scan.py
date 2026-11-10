"""Index a shoot folder into stacks (RAW + JPG + sidecar that belong together)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

RAW_EXTS = {
    ".nef", ".nrw", ".cr2", ".cr3", ".crw", ".arw", ".srf", ".sr2", ".dng",
    ".raf", ".orf", ".rw2", ".raw", ".srw", ".pef", ".iiq", ".3fr", ".fff",
    ".mos", ".mrw", ".x3f", ".gpr",
}
JPG_EXTS = {".jpg", ".jpeg", ".jpe", ".heic", ".heif", ".png", ".tif", ".tiff", ".webp"}
SIDECAR_EXTS = {".xmp", ".aae", ".dop", ".pp3", ".cos", ".on1"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".mts", ".braw", ".r3d"}

_LAST_DIGITS = re.compile(r"(\d+)(?!.*\d)", re.S)
_ALL_KNOWN = RAW_EXTS | JPG_EXTS | VIDEO_EXTS


@dataclass
class Stack:
    """Every file that travels together for one frame."""

    base: str
    dirpath: Path
    raws: list[Path] = field(default_factory=list)
    jpgs: list[Path] = field(default_factory=list)
    sidecars: list[Path] = field(default_factory=list)
    videos: list[Path] = field(default_factory=list)

    @property
    def frame(self) -> int | None:
        m = _LAST_DIGITS.search(self.base)
        return int(m.group(1)) if m else None

    @property
    def files(self) -> list[Path]:
        return [*self.raws, *self.jpgs, *self.videos, *self.sidecars]

    @property
    def primary(self) -> Path | None:
        """Best file to read pixels from: RAW first, then JPG."""
        if self.raws:
            return self.raws[0]
        if self.jpgs:
            return self.jpgs[0]
        return self.videos[0] if self.videos else None

    @property
    def size(self) -> int:
        total = 0
        for f in self.files:
            try:
                total += f.stat().st_size
            except OSError:
                pass
        return total

    def __repr__(self) -> str:
        return f"<Stack {self.base} {len(self.files)} files>"


@dataclass
class Index:
    root: Path
    stacks: list[Stack] = field(default_factory=list)
    by_frame: dict[int, list[Stack]] = field(default_factory=dict)
    by_base: dict[str, Stack] = field(default_factory=dict)
    skipped: list[Path] = field(default_factory=list)

    @property
    def frames(self) -> list[int]:
        return sorted(self.by_frame)

    def counts(self) -> dict[str, int]:
        return {
            "stacks": len(self.stacks),
            "raw": sum(len(s.raws) for s in self.stacks),
            "jpg": sum(len(s.jpgs) for s in self.stacks),
            "sidecar": sum(len(s.sidecars) for s in self.stacks),
            "video": sum(len(s.videos) for s in self.stacks),
        }


def _base_of(path: Path) -> str:
    """Filename without extension, unwrapping doubled sidecar names.

    DSC_1234.NEF.xmp -> DSC_1234       (Bridge/Lightroom style)
    DSC_1234.xmp     -> DSC_1234
    """
    stem = path.stem
    if path.suffix.lower() in SIDECAR_EXTS:
        inner = Path(stem)
        if inner.suffix.lower() in _ALL_KNOWN:
            stem = inner.stem
    return stem


def scan(root, recursive: bool = False, follow_hidden: bool = False) -> Index:
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"not a folder: {root}")

    idx = Index(root=root)
    groups: dict[tuple[str, str], Stack] = {}

    walker = root.rglob("*") if recursive else root.glob("*")
    for path in walker:
        if not path.is_file():
            continue
        name = path.name
        if not follow_hidden and (name.startswith(".") or name.startswith("._")):
            continue
        ext = path.suffix.lower()
        if ext not in RAW_EXTS and ext not in JPG_EXTS and ext not in SIDECAR_EXTS \
                and ext not in VIDEO_EXTS:
            idx.skipped.append(path)
            continue

        base = _base_of(path)
        key = (str(path.parent).lower(), base.lower())
        stack = groups.get(key)
        if stack is None:
            stack = Stack(base=base, dirpath=path.parent)
            groups[key] = stack

        if ext in RAW_EXTS:
            stack.raws.append(path)
        elif ext in JPG_EXTS:
            stack.jpgs.append(path)
        elif ext in VIDEO_EXTS:
            stack.videos.append(path)
        else:
            stack.sidecars.append(path)

    idx.stacks = sorted(groups.values(), key=lambda s: (str(s.dirpath).lower(), s.base.lower()))
    for stack in idx.stacks:
        idx.by_base.setdefault(stack.base.lower(), stack)
        f = stack.frame
        if f is not None:
            idx.by_frame.setdefault(f, []).append(stack)
    return idx


@dataclass
class Match:
    number: int
    stacks: list[Stack]
    status: str  # "ok" | "missing" | "ambiguous"
    note: str = ""


def resolve(idx: Index, sel, prefer_prefix: bool = True) -> list[Match]:
    """Map requested frame numbers onto real stacks in the folder."""
    matches: list[Match] = []
    for n in sel.numbers:
        cands = list(idx.by_frame.get(n, []))
        note = ""

        if not cands:
            # Some cameras glue a roll number onto the frame number, so
            # P1010007.RW2 is frame 7 of roll 101. When the plain number finds
            # nothing, fall back to matching the end of the digits. Three digits
            # minimum, otherwise "1" would match half the card.
            typed = sel.digits_for(n)
            tail = typed.lstrip("0") or "0"
            if len(typed) >= 3:
                cands = [s for s in idx.stacks
                         if s.frame is not None and str(s.frame).endswith(tail)]
            if not cands:
                matches.append(Match(n, [], "missing"))
                continue
            note = f"matched on the ending '{typed}'"

        if len(cands) > 1 and prefer_prefix:
            hint = sel.prefix_for(n)
            if hint:
                narrowed = [
                    s for s in cands if s.base.lower().startswith(hint.lower())
                ]
                if narrowed:
                    note = f"narrowed by prefix '{hint}'"
                    cands = narrowed

        if len(cands) > 1:
            names = ", ".join(s.base for s in cands)
            matches.append(Match(n, cands, "ambiguous", f"matches {names}"))
        else:
            matches.append(Match(n, cands, "ok", note))
    return matches
