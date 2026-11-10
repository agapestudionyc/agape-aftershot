"""Your thumbs up and down on focus, and the softness bar they teach Sift.

Sift calls a frame soft when it falls below a fraction of the shoot's median
sharpness. That fraction is a matter of taste, not physics: a frame one
photographer calls "a touch soft, fine" another bins. So instead of a slider,
every thumbs up ("sharp enough for me") or thumbs down ("too soft for me") you
give while looking at a frame up close is kept here, with the frame's measured
ratio, and the bar is set to the one that disagrees with you least.

Votes live in ~/.cullkit, not the shoot folder, because the taste is yours and
carries from one shoot to the next.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

VOTES = Path.home() / ".cullkit" / "focus_votes.json"
DEFAULT_RATIO = 0.65
# Enough of each side that one stray tap does not move the bar.
MIN_EACH = 3
MIN_TOTAL = 8
LO, HI = 0.30, 1.20
# A thumbs down on a frame at or above the shoot's median is not about overall
# softness - the face missed focus, or something moved - so it says nothing
# about where the softness bar belongs and would only drag it upward.
NOT_ABOUT_SOFTNESS = 1.0


def _load():
    try:
        return json.loads(VOTES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(votes):
    VOTES.parent.mkdir(parents=True, exist_ok=True)
    tmp = VOTES.with_suffix(".tmp")
    tmp.write_text(json.dumps(votes, indent=1), encoding="utf-8")
    tmp.replace(VOTES)


def _key(folder, stem):
    return f"{str(folder).rstrip(chr(92) + '/').lower()}|{stem}"


def get(folder, stem):
    return _load().get(_key(folder, stem))


def record(folder, stem, vote, blur_ratio=None, verdict=None):
    """Keep one vote per frame. `vote` is 'sharp', 'soft', or None to take it back."""
    votes = _load()
    k = _key(folder, stem)
    if vote is None:
        votes.pop(k, None)
    else:
        if vote not in ("sharp", "soft"):
            raise ValueError(f"vote must be sharp or soft, not {vote!r}")
        ok = isinstance(blur_ratio, (int, float)) and not math.isnan(blur_ratio)
        votes[k] = {"folder": str(folder), "stem": stem, "vote": vote,
                    "blur_ratio": float(blur_ratio) if ok else None,
                    "verdict": verdict, "when": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _save(votes)
    return summary(votes)


def learned_blur_ratio(votes=None):
    """The bar that best agrees with your votes, or None until there are enough."""
    votes = _load() if votes is None else votes
    sharp = [v["blur_ratio"] for v in votes.values()
             if v.get("vote") == "sharp" and v.get("blur_ratio") is not None]
    soft = [v["blur_ratio"] for v in votes.values()
            if v.get("vote") == "soft" and v.get("blur_ratio") is not None
            and v["blur_ratio"] < NOT_ABOUT_SOFTNESS]
    if len(sharp) < MIN_EACH or len(soft) < MIN_EACH or len(sharp) + len(soft) < MIN_TOTAL:
        return None
    grid = [round(LO + i * 0.01, 2) for i in range(int(round((HI - LO) / 0.01)) + 1)]
    errs = [sum(1 for r in soft if r >= t) + sum(1 for r in sharp if r < t) for t in grid]
    best = min(errs)
    # Every bar in the winning run agrees with you equally; the middle of the
    # run sits furthest from both sides, so it is the least fragile choice.
    winners = [t for t, e in zip(grid, errs) if e == best]
    ratio = winners[len(winners) // 2]
    return {"ratio": ratio, "votes": len(sharp) + len(soft), "errors": best}


def summary(votes=None):
    votes = _load() if votes is None else votes
    n_sharp = sum(1 for v in votes.values() if v.get("vote") == "sharp")
    n_soft = sum(1 for v in votes.values() if v.get("vote") == "soft")
    learned = learned_blur_ratio(votes)
    return {"sharp": n_sharp, "soft": n_soft,
            "ratio": learned["ratio"] if learned else DEFAULT_RATIO,
            "learned": bool(learned)}
