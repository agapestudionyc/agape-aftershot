"""The vibes: what they are called, what they look like, and what they play.

A vibe is a mood you put the room in while you work - a name, an icon, a colour
and a playlist. There were three of them and they were written into the page:
the names, the shapes of the icons, the colours, and the playlists worked out
from the first word of each filename in `web/audio`. Adding a fourth, or
renaming one, or putting your own music in one, meant editing the program.

So they move out here. Four of them at most - past four the button stops being
a button you tap through and becomes a menu, and this is meant to be something
you hit with a thumb without looking.

**The music is copied in, not linked to.** A playlist that points at files on a
drive is a playlist that breaks the first time the drive is unplugged or the
folder is tidied, and it breaks silently, in the middle of a cull. Each track is
converted once, on the way in, into one small file that lives beside the rest of
the program's own state - so a vibe is a thing you made rather than a set of
directions to things you hope are still there.

Nothing here ever touches a photograph, and nothing here is ever sent anywhere.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import uuid

from pathlib import Path

HOME = Path.home() / ".cullkit"
STORE = HOME / "vibes.json"
AUDIO = HOME / "vibes"

MAX_VIBES = 4
# A minute of music is about a megabyte once it has been through the converter.
# The cap is on what arrives, not on what is kept, because what arrives is what
# has to be held in memory while it is read.
MAX_UPLOAD = 60 * 1024 * 1024

_lock = threading.Lock()

# What a browser will play without help. Anything else - and anything larger
# than it needs to be - goes through the converter.
PLAYABLE = {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac"}

# The three that were written into the page, kept as what a fresh install
# starts with. Same names, same colours, same icons, same music - so nothing
# changes for somebody who never opens the settings.
DEFAULTS = [
    {"id": "afro", "name": "Afrobeats", "icon": "camera", "colour": "#ffb84d",
     "says": "afrobeats · lagos hours 🌍", "builtin": "afro"},
    {"id": "metal", "name": "Metal", "icon": "skull", "colour": "#ff4d4d",
     "says": "metal · neck damage 🤘", "builtin": "metal"},
    {"id": "schranz", "name": "Schranz", "icon": "bolt", "colour": "#4dd2ff",
     "says": "schranz · 154 bpm and no mercy ⚡", "builtin": "schranz"},
]


def bitrate(path) -> int:
    """What the file is already encoded at, in bits per second. 0 if unknown.

    Without this, "compact it" made things bigger: a 2.5 MB MP3 encoded at
    96 kbps came back out as a 4.1 MB AAC, because it was re-encoded up to
    128 and re-encoding never recovers what the first encoder threw away. The
    only honest version of compacting is to leave alone anything already
    smaller than what we would produce.
    """
    ff = shutil.which("ffprobe")
    if not ff:
        return 0
    try:
        run = subprocess.run(
            [ff, "-v", "error", "-show_entries", "format=bit_rate",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=60)
        return int((run.stdout or "0").strip() or 0)
    except Exception:
        return 0


# What a track is re-encoded to when it is worth re-encoding at all. Anything
# already at or under this is copied across untouched.
TARGET_BPS = 128_000


def _yt_clean(items):
    from . import ytm
    return ytm.clean(items)


def _slug(text):
    """A file-safe name that a person can still read in a folder listing."""
    out = re.sub(r"[^A-Za-z0-9._ -]+", "", str(text or "")).strip()
    return (out or "track")[:60]


def load() -> list:
    """Every vibe, in the order they are tapped through."""
    try:
        got = json.loads(STORE.read_text(encoding="utf-8"))
        vibes = got.get("vibes") if isinstance(got, dict) else got
        if isinstance(vibes, list) and vibes:
            return vibes[:MAX_VIBES]
        # Deleted down to none is a choice, not a missing file. The three that
        # shipped used to walk back in the moment the last vibe was deleted,
        # so clearing the list out to start again could not be done at all.
        if isinstance(got, dict) and vibes == []:
            return []
    except Exception:
        pass
    return [dict(v) for v in DEFAULTS]


def save(vibes) -> list:
    """Write them back, having first refused anything that would not work.

    Everything here arrives from a browser, which means it arrives from
    somewhere that can be wrong. A colour that is not a colour, a name the
    length of a paragraph, or a fifth vibe are all things to fix quietly rather
    than store and fall over later.
    """
    clean = []
    for v in (vibes or [])[:MAX_VIBES]:
        if not isinstance(v, dict):
            continue
        vid = str(v.get("id") or "").strip() or uuid.uuid4().hex[:8]
        vid = re.sub(r"[^a-z0-9_-]+", "", vid.lower())[:24] or uuid.uuid4().hex[:8]
        colour = str(v.get("colour") or "").strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", colour):
            colour = "#3dff87"
        clean.append({
            "id": vid,
            "name": (str(v.get("name") or "").strip() or "Vibe")[:24],
            "icon": re.sub(r"[^a-z-]+", "", str(v.get("icon") or "note"))[:24] or "note",
            "colour": colour,
            "says": str(v.get("says") or "")[:80],
            # `builtin` marks a vibe still playing out of web/audio rather than
            # out of its own folder. Kept for the three that shipped, dropped
            # the moment somebody puts their own music in one.
            **({"builtin": str(v["builtin"])[:24]} if v.get("builtin") else {}),
            # A YouTube vibe: ids chosen from the catalogue, played by
            # YouTube's own player. See ytm.py for what that does and does not
            # mean. While this list has anything in it, it is what plays.
            **({"yt": _yt_clean(v.get("yt"))} if v.get("yt") else {}),
        })
    seen, out = set(), []
    for v in clean:
        if v["id"] in seen:
            v["id"] = uuid.uuid4().hex[:8]
        seen.add(v["id"])
        out.append(v)
    with _lock:
        HOME.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps({"vibes": out}, indent=2, ensure_ascii=False),
                         encoding="utf-8")
    return out


def folder(vid) -> Path:
    return AUDIO / re.sub(r"[^a-z0-9_-]+", "", str(vid).lower())[:24]


def tracks(vid) -> list:
    """What one vibe plays, in the order it was added."""
    d = folder(vid)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.iterdir(), key=lambda p: p.stat().st_mtime):
        if f.is_file() and f.suffix.lower() in PLAYABLE:
            out.append({"file": f.name, "name": f.stem,
                        "bytes": f.stat().st_size})
    return out


def forget_track(vid, name) -> bool:
    f = folder(vid) / Path(str(name)).name
    try:
        if f.is_file() and f.parent == folder(vid):
            f.unlink()
            return True
    except OSError:
        pass
    return False


def forget_all(vid) -> None:
    shutil.rmtree(folder(vid), ignore_errors=True)


def add_track(vid, filename, data: bytes) -> dict:
    """Take one uploaded file and leave one playable track behind.

    The conversion is the point. Whatever a phone or a laptop hands over -
    a 40 MB WAV off a sampler, an AIFF, an M4A recorded at a bitrate nobody
    needs for a room speaker - comes out as one small file at a size that
    streams over the wifi to a phone in the next room without stuttering.

    ffmpeg does the work when it is there. When it is not, a file the browser
    can already play is kept as it arrived, and one it cannot is refused with
    a sentence saying why rather than stored as something that will simply
    never make a sound.
    """
    d = folder(vid)
    d.mkdir(parents=True, exist_ok=True)
    stem = _slug(Path(str(filename or "track")).stem)
    raw_suffix = Path(str(filename or "")).suffix.lower()

    tmp = d / f".incoming-{uuid.uuid4().hex[:8]}{raw_suffix or '.bin'}"
    tmp.write_bytes(data)
    try:
        ff = shutil.which("ffmpeg")
        # Already small enough, and already something a browser plays: copying
        # it is both faster and better than any conversion could be.
        rate = bitrate(tmp)
        if raw_suffix in PLAYABLE and rate and rate <= TARGET_BPS * 1.1:
            out = d / f"{stem}{raw_suffix}"
            n = 2
            while out.exists():
                out = d / f"{stem} ({n}){raw_suffix}"
                n += 1
            tmp.replace(out)
            return {"ok": True, "file": out.name, "name": out.stem,
                    "bytes": out.stat().st_size, "was": len(data),
                    "converted": False, "kept": True}
        if ff:
            out = d / f"{stem}.m4a"
            n = 2
            while out.exists():
                out = d / f"{stem} ({n}).m4a"
                n += 1
            # -vn drops cover art, which otherwise rides along as a video
            # stream and makes the file several times the size for nothing.
            run = subprocess.run(
                [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(tmp),
                 "-vn", "-ac", "2", "-c:a", "aac",
                 # Never above what the source already is: re-encoding upwards
                 # spends disk to store nothing but the first encoder's losses.
                 "-b:a", str(min(TARGET_BPS, rate) if rate else TARGET_BPS),
                 str(out)],
                capture_output=True, text=True, timeout=600)
            if run.returncode == 0 and out.is_file() and out.stat().st_size:
                return {"ok": True, "file": out.name, "name": out.stem,
                        "bytes": out.stat().st_size, "was": len(data),
                        "converted": True}
            out.unlink(missing_ok=True)
            return {"ok": False,
                    "error": "that file could not be converted — "
                             + ((run.stderr or "").strip().splitlines() or
                                ["ffmpeg would not read it"])[-1][:160]}
        if raw_suffix in PLAYABLE:
            out = d / f"{stem}{raw_suffix}"
            n = 2
            while out.exists():
                out = d / f"{stem} ({n}){raw_suffix}"
                n += 1
            tmp.replace(out)
            return {"ok": True, "file": out.name, "name": out.stem,
                    "bytes": out.stat().st_size, "was": len(data),
                    "converted": False}
        return {"ok": False,
                "error": "this computer has no converter installed, so only "
                         "MP3, M4A, OGG, WAV and FLAC can be added"}
    finally:
        tmp.unlink(missing_ok=True)


def size_on_disk() -> dict:
    """How much room the music takes, for the panel that clears things."""
    total, files = 0, 0
    if AUDIO.is_dir():
        for f in AUDIO.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
                    files += 1
            except OSError:
                pass
    return {"bytes": total, "files": files, "path": str(AUDIO)}
