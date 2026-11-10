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
