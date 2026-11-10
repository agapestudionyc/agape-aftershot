"""Move, copy or link stacks into a destination folder - safely.

Rules, because these files are irreplaceable:
  * nothing happens without --apply; the default is always a dry run
  * a whole stack travels together (RAW + JPG + XMP sidecar)
  * an existing destination file is never overwritten
  * every applied run writes an undo journal that reverses it
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

MOVE, COPY, LINK = "move", "copy", "link"


@dataclass
class Op:
    src: Path
    dst: Path
    mode: str
    status: str = "pending"   # pending|done|skipped|failed|conflict
    note: str = ""

    def as_json(self):
        return {
            "src": str(self.src), "dst": str(self.dst),
            "mode": self.mode, "status": self.status, "note": self.note,
        }


@dataclass
class Plan:
    dest: Path
    mode: str
    ops: list = field(default_factory=list)
    bytes_total: int = 0

    def summary(self):
        by = {}
        for op in self.ops:
            by[op.status] = by.get(op.status, 0) + 1
        return by


def _same_file(a: Path, b: Path) -> bool:
    try:
        sa, sb = a.stat(), b.stat()
    except OSError:
        return False
    return sa.st_size == sb.st_size and abs(sa.st_mtime - sb.st_mtime) < 2


def _unique(dst: Path) -> Path:
    """dst -> dst (2) -> dst (3) ... never clobber."""
    if not dst.exists():
        return dst
    stem, suffix, parent = dst.stem, dst.suffix, dst.parent
    n = 2
    while True:
        cand = parent / f"{stem} ({n}){suffix}"
        if not cand.exists():
            return cand
        n += 1


def build_plan(stacks, dest, mode=MOVE, flatten=True, on_conflict="rename") -> Plan:
    """Work out every file operation before touching anything."""
    dest = Path(dest)
    plan = Plan(dest=dest, mode=mode)
    claimed = set()

    for stack in stacks:
        for src in stack.files:
            target_dir = dest if flatten else dest / src.parent.name
            dst = target_dir / src.name

            if dst.exists() or dst in claimed:
                if dst.exists() and _same_file(src, dst):
                    plan.ops.append(Op(src, dst, mode, "skipped", "already there"))
                    continue
                if on_conflict == "skip":
                    plan.ops.append(Op(src, dst, mode, "skipped", "name taken"))
                    continue
                if on_conflict == "fail":
                    plan.ops.append(Op(src, dst, mode, "conflict", "name taken"))
                    continue
                new_dst = _unique(dst)
                while new_dst in claimed:
                    new_dst = _unique(new_dst)
                plan.ops.append(
                    Op(src, new_dst, mode, "pending", f"renamed, {dst.name} was taken")
                )
                claimed.add(new_dst)
                plan.bytes_total += _size(src)
                continue

            claimed.add(dst)
            plan.ops.append(Op(src, dst, mode))
            plan.bytes_total += _size(src)
    return plan


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _sha256(p: Path, chunk=1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def execute(plan: Plan, apply=False, verify=False, journal_dir=None, on_step=None):
    """Run the plan. Without apply=True this is a no-op that just reports."""
    if not apply:
        return None

    plan.dest.mkdir(parents=True, exist_ok=True)
    done = []

    for op in plan.ops:
        if op.status != "pending":
            continue
        try:
            op.dst.parent.mkdir(parents=True, exist_ok=True)
            if op.mode == MOVE:
                shutil.move(str(op.src), str(op.dst))
            elif op.mode == COPY:
                shutil.copy2(str(op.src), str(op.dst))
            elif op.mode == LINK:
                try:
                    os.link(str(op.src), str(op.dst))
                except OSError:
                    # different volume, or a filesystem without hard links
                    shutil.copy2(str(op.src), str(op.dst))
                    op.note = (op.note + "; copied, link unavailable").lstrip("; ")
            else:
                raise ValueError(f"unknown mode {op.mode}")

            if verify and op.mode != MOVE:
                if _sha256(op.src) != _sha256(op.dst):
                    op.status, op.note = "failed", "checksum mismatch"
                    continue
            op.status = "done"
            done.append(op)
        except Exception as exc:
            op.status = "failed"
            op.note = f"{type(exc).__name__}: {exc}"
        if on_step:
            on_step(op)

    journal = None
    if done:
        journal_dir = Path(journal_dir or (plan.dest / "_cullkit"))
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal = journal_dir / f"undo-{time.strftime('%Y%m%d-%H%M%S')}.json"
        journal.write_text(
            json.dumps(
                {
                    "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "dest": str(plan.dest),
                    "mode": plan.mode,
                    "ops": [op.as_json() for op in done],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return journal


def undo(journal_path, apply=False):
    """Reverse an applied run. Moves go back; copies and links are removed."""
    data = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    results = []
    for rec in reversed(data.get("ops", [])):
        src, dst, mode = Path(rec["src"]), Path(rec["dst"]), rec["mode"]
        if not dst.exists():
            results.append((dst, "missing", "nothing to undo"))
            continue
        if mode == MOVE:
            if src.exists():
                results.append((dst, "blocked", "original path is occupied again"))
                continue
            if apply:
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst), str(src))
            results.append((dst, "restored", str(src)))
        else:
            if apply:
                dst.unlink()
            results.append((dst, "removed", ""))
    return results
