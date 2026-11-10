"""What happened, and where everything went.

A small JSON record so the program can be closed and reopened weeks later and
still answer: which shoots exist, what was culled out of them, and which frames
were picked for delivery. Paths are stored absolute because folders get moved
around on an external drive; anything missing is reported rather than assumed.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

LEDGER = Path.home() / ".cullkit" / "ledger.json"
CONFIG = Path.home() / ".cullkit" / "config.json"

DEFAULT_CONFIG = {
    "archive_root": "",      # where imported cards land
    "work_root": "",         # where selects go when you start editing
    "gap_minutes": 45,
    "scene_threshold": 0.42,
}


def _load(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return dict(default)
    return dict(default)


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(_load(CONFIG, {}))
    return cfg


def save_config(cfg):
    _save(CONFIG, cfg)
    return cfg


def load():
    d = _load(LEDGER, {"imports": [], "shoots": []})
    d.setdefault("imports", [])
    d.setdefault("shoots", [])
    return d


def save(d):
    _save(LEDGER, d)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def record_import(source, dest, copied, skipped, failed, bytes_copied):
    d = load()
    d["imports"].append({
        "when": _now(), "source": str(source), "dest": str(dest),
        "copied": copied, "skipped": skipped, "failed": failed,
        "bytes": bytes_copied,
    })
    save(d)
    return d["imports"][-1]


def record_shoot(path, name=None, count=None, parent=None, people=None,
                 start=None, camera=None):
    """Create or update a shoot entry, keyed on its folder path."""
    d = load()
    key = str(Path(path).resolve())
    for s in d["shoots"]:
        if s["path"] == key:
            entry = s
            break
    else:
        entry = {"path": key, "created": _now()}
        d["shoots"].append(entry)

    entry["name"] = name or entry.get("name") or Path(path).name
    if count is not None:
        entry["count"] = count
    if parent:
        entry["parent"] = str(parent)
    if people:
        entry["people"] = people
    if start:
        entry["shot_on"] = start
    if camera:
        entry["camera"] = camera
    entry["updated"] = _now()
    save(d)
    return entry


def record_cull(path, keep, review, reject):
    d = load()
    key = str(Path(path).resolve())
    for s in d["shoots"]:
        if s["path"] == key:
            s["cull"] = {"when": _now(), "keep": keep,
                         "review": review, "reject": reject}
            s["updated"] = _now()
            save(d)
            return s
    return None


def record_cache(path):
    """Remember that a folder now holds a _cullkit cache.

    Without this, clearing space means walking whole drives hoping to find them.
    With it, the program knows exactly where it has been - including folders you
    later moved somewhere a scan would never look.
    """
    d = load()
    known = d.setdefault("caches", [])
    key = str(Path(path).resolve())
    if key not in known:
        known.append(key)
        save(d)
    return known


def caches(only_existing=True):
    """Every folder known to hold a cache."""
    out = []
    for c in load().get("caches", []):
        if only_existing and not (Path(c) / "_cullkit").is_dir():
            continue
        out.append(c)
    return out


def forget_caches(paths):
    """Drop entries whose cache is gone, so the list stays honest."""
    d = load()
    gone = {str(p) for p in paths}
    d["caches"] = [c for c in d.get("caches", []) if c not in gone]
    save(d)
    return d["caches"]


def record_selects(path, numbers, dest):
    d = load()
    key = str(Path(path).resolve())
    for s in d["shoots"]:
        if s["path"] == key:
            hist = s.setdefault("selects", [])
            hist.append({"when": _now(), "count": len(numbers),
                         "dest": str(dest), "numbers": sorted(numbers)})
            s["updated"] = _now()
            save(d)
            return s
    return None


def relocate(old, new):
    """A shoot folder moved as a whole: point every record at where it went."""
    d = load()
    o, n = str(Path(old).resolve()), str(Path(new).resolve())

    def moved(p):
        p = str(p)
        if p == o:
            return n
        if p.startswith(o + "\\") or p.startswith(o + "/"):
            return n + p[len(o):]
        return p

    for s in d["shoots"]:
        if s["path"] == o:
            s["moved_from"] = o
            s["updated"] = _now()
        s["path"] = moved(s["path"])
        if s.get("parent"):
            s["parent"] = moved(s["parent"])
    d["caches"] = [moved(c) for c in d.get("caches", [])]
    save(d)


def shoots(only_existing=False):
    out = []
    for s in load()["shoots"]:
        s = dict(s)
        s["exists"] = Path(s["path"]).is_dir()
        if only_existing and not s["exists"]:
            continue
        out.append(s)
    return out


def find(term):
    """Match a shoot by name fragment or path, case-insensitive."""
    term = (term or "").strip().lower()
    if not term:
        return []
    hits = []
    for s in shoots():
        if term in s.get("name", "").lower() or term in s["path"].lower():
            hits.append(s)
    return hits
