"""YouTube Music, as a catalogue to choose from.

`ytmusicapi` answers "what is there": search for a song or a playlist, read
what a playlist holds. That is all this module asks of it.

It does **not** fetch audio, and nothing in this program does. A track chosen
here is stored as its YouTube id and played by YouTube's own embedded player,
in the page, the way YouTube allows - which is also why a YouTube vibe needs
the internet, can show an advert, and has a small visible player while it is
on. Pulling the stream out and playing it ourselves would get round all three
and is the one thing this deliberately will not do.

The library is optional. Without it everything else works and these two
calls say so.
"""
from __future__ import annotations

import re
import threading

_lock = threading.Lock()
_client = None
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _yt():
    global _client
    with _lock:
        if _client is None:
            from ytmusicapi import YTMusic      # ImportError is the caller's news
            _client = YTMusic()
        return _client


def _track(x) -> dict | None:
    vid = str(x.get("videoId") or "")
    if not VIDEO_ID.match(vid):
        return None
    artists = ", ".join(a.get("name", "") for a in (x.get("artists") or [])
                        if isinstance(a, dict) and a.get("name"))
    thumbs = x.get("thumbnails") or []
    return {"id": vid, "title": str(x.get("title") or "")[:120],
            "artist": artists[:80], "duration": str(x.get("duration") or ""),
            "thumb": (thumbs[-1].get("url") if thumbs else "") or ""}


def search(q: str, kind: str = "songs") -> dict:
    q = (q or "").strip()
    if not q:
        return {"items": []}
    try:
        yt = _yt()
    except ImportError:
        return {"error": "ytmusicapi is not installed on this computer"}
    try:
        if kind == "playlists":
            out = []
            for x in yt.search(q, filter="playlists", limit=12):
                bid = str(x.get("browseId") or "")
                if not bid:
                    continue
                thumbs = x.get("thumbnails") or []
                out.append({"playlist": bid[2:] if bid.startswith("VL") else bid,
                            "title": str(x.get("title") or "")[:120],
                            "artist": str(x.get("author") or "")[:80],
                            "count": x.get("itemCount"),
                            "thumb": (thumbs[-1].get("url") if thumbs else "") or ""})
            return {"items": out}
        rows = yt.search(q, filter="songs", limit=20)
        return {"items": [t for t in (_track(x) for x in rows) if t]}
    except Exception as exc:
        return {"error": f"YouTube Music did not answer ({type(exc).__name__})"}


def playlist(pid: str, limit: int = 100) -> dict:
    pid = re.sub(r"[^A-Za-z0-9_-]+", "", str(pid or ""))[:80]
    if not pid:
        return {"error": "no playlist"}
    try:
        yt = _yt()
    except ImportError:
        return {"error": "ytmusicapi is not installed on this computer"}
    try:
        got = yt.get_playlist(pid, limit=limit)
    except Exception as exc:
        return {"error": f"YouTube Music did not answer ({type(exc).__name__})"}
    tracks = [t for t in (_track(x) for x in (got.get("tracks") or [])) if t]
    return {"title": str(got.get("title") or "")[:120], "items": tracks[:limit]}


def clean(items, cap: int = 150) -> list:
    """What a vibe is allowed to store: ids that are ids, text that is short."""
    out, seen = [], set()
    for x in items or []:
        if not isinstance(x, dict):
            continue
        vid = str(x.get("id") or "")
        if not VIDEO_ID.match(vid) or vid in seen:
            continue
        seen.add(vid)
        out.append({"id": vid, "title": str(x.get("title") or "")[:120],
                    "artist": str(x.get("artist") or "")[:80]})
        if len(out) >= cap:
            break
    return out


"""The audio, kept on this computer, played by the app's own player.

Asked for on 2026-11-10, and a deliberate change of mind from the top of this
file: the embedded player showed a video in a small window, stopped when the
phone locked, and was not the app's player. So a track chosen from the
catalogue is now fetched once, as audio only, with `yt-dlp`, and kept beside
the program's other state. After that it is a file like any other track.

Two things to know before touching this:

- It is against YouTube's terms of service, which is why it is an optional
  extra (`pip install yt-dlp` into the venv) and not in `requirements.txt`.
  Without the library nothing here runs and the page falls back to the
  embedded player, exactly as before.
- YouTube changes and `yt-dlp` follows. When fetching starts failing, the fix
  is `pip install -U yt-dlp`, not anything in this file.
"""
import time
from pathlib import Path

CACHE = Path.home() / ".cullkit" / "yt-audio"
_fetching = {}                       # video id -> the lock its download holds
_fetch_lock = threading.Lock()
_queue_lock = threading.Lock()
_queue = []
_worker = {"on": False}
_failed = {}                         # video id -> when it last failed


def can_fetch() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def have(vid: str):
    """The file for this track if it is already here, else None."""
    if not VIDEO_ID.match(str(vid or "")):
        return None
    for ext in (".m4a", ".webm", ".opus", ".mp3"):
        f = CACHE / f"{vid}{ext}"
        if f.is_file() and f.stat().st_size > 1024:
            return f
    return None


def audio(vid: str):
    """(file, None) once the audio is on this computer, or (None, why not).

    One download per track at a time: two screens asking for the same song,
    or the player asking for one the queue is halfway through, wait for the
    same file rather than fetching it twice.
    """
    vid = str(vid or "")
    if not VIDEO_ID.match(vid):
        return None, "not a track"
    f = have(vid)
    if f:
        return f, None
    try:
        import yt_dlp
    except ImportError:
        return None, "yt-dlp is not installed on this computer"
    with _fetch_lock:
        lock = _fetching.setdefault(vid, threading.Lock())
    with lock:
        f = have(vid)
        if f:
            return f, None
        # A track that has just failed is not tried again on every poke.
        if time.time() - _failed.get(vid, 0) < 60:
            return None, "that song could not be fetched"
        CACHE.mkdir(parents=True, exist_ok=True)
        opts = {"format": "bestaudio[ext=m4a]/bestaudio",
                "outtmpl": str(CACHE / "%(id)s.%(ext)s"),
                "quiet": True, "no_warnings": True, "noprogress": True,
                "noplaylist": True, "max_filesize": 80 * 1024 * 1024,
                "socket_timeout": 20, "retries": 2}
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                y.download([f"https://music.youtube.com/watch?v={vid}"])
        except Exception as exc:
            _failed[vid] = time.time()
            return None, f"that song could not be fetched ({type(exc).__name__})"
        f = have(vid)
        if not f:
            _failed[vid] = time.time()
            return None, "that song could not be fetched"
        return f, None


def prefetch(ids) -> None:
    """Fetch these behind whoever is adding them, one at a time.

    So a song added from the search is already here by the time it is its
    turn to play, and the first play is not the one that waits.
    """
    if not can_fetch():
        return
    with _queue_lock:
        for vid in ids or []:
            if VIDEO_ID.match(str(vid)) and vid not in _queue and not have(vid):
                _queue.append(vid)
        if _worker["on"] or not _queue:
            return
        _worker["on"] = True

    def run():
        while True:
            with _queue_lock:
                if not _queue:
                    _worker["on"] = False
                    return
                vid = _queue.pop(0)
            audio(vid)

    threading.Thread(target=run, daemon=True, name="yt-audio").start()


def prune(keep) -> int:
    """Drop the audio of every track no vibe holds any more."""
    keep = set(keep or [])
    gone = 0
    if not CACHE.is_dir():
        return 0
    for f in CACHE.iterdir():
        if f.is_file() and f.stem not in keep and not f.name.endswith(".part"):
            try:
                f.unlink()
                gone += 1
            except OSError:
                pass
    return gone


def status(ids) -> dict:
    """Which of these are already on this computer."""
    return {vid: bool(have(vid)) for vid in ids or []}
