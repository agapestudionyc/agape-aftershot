"""Group a shoot into the runs of near-identical frames it is really made of.

A shoot is not 315 different pictures. It is thirty-odd setups, each shot ten
or twenty times while the model shifts a hand or the light moves. Deciding
which of the twenty to keep is the actual work, and it is much easier when the
twenty are sitting together in one folder instead of scattered down a list.

Two frames belong together when they look alike *and* were taken close in time.
Appearance alone would merge two separate returns to the same wall an hour
apart; time alone would split a pose the moment the model paused.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import numpy as np

# A frame is compared on a small greyscale thumbnail, which captures pose and
# composition, plus a coarse colour histogram, which captures wardrobe and
# backdrop. Structure is weighted higher: the outfit rarely changes mid-pose.
THUMB = 24
W_STRUCT, W_COLOUR = 0.7, 0.3
# Calibrated on 267 real frames: consecutive frames sit around 0.93 similar,
# so anything stricter than this splits on every shutter press. 0.62 turned
# 267 frames into 29 runs of about six - one folder per setup.
DEFAULT_SIM = 0.62
DEFAULT_GAP = 90          # seconds of stillness that end a run
MIN_GROUP = 1
MAX_RUN = 16              # above this a run stops being a shortlist


@dataclass
class Pose:
    index: int
    stacks: list = field(default_factory=list)
    best: object = None       # the sharpest frame in the run
    sharpness: dict = field(default_factory=dict)
    start=None
    end=None

    @property
    def count(self):
        return len(self.stacks)

    @property
    def frames(self):
        return [s.frame for s in self.stacks if s.frame is not None]

    def label(self):
        fr = self.frames
        span = f"{min(fr)}-{max(fr)}" if fr else "?"
        return f"Pose {self.index:02d}  ({self.count} frames, {span})"


def signature(path):
    """-> (structure vector, colour vector), both unit length."""
    import cv2

    img = cv2.imread(str(path))
    if img is None:
        return None
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(grey, (THUMB, THUMB), interpolation=cv2.INTER_AREA)
    s = small.astype(np.float32).ravel()
    s -= s.mean()
    n = np.linalg.norm(s)
    s = s / n if n else s

    hsv = cv2.cvtColor(cv2.resize(img, (96, 96)), cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1], None, [12, 6], [0, 180, 0, 256])
    h = cv2.normalize(h, h).flatten().astype(np.float32)
    n = np.linalg.norm(h)
    h = h / n if n else h
    return s, h


def similarity(a, b):
    if a is None or b is None:
        return 0.0
    return float(W_STRUCT * np.dot(a[0], b[0]) + W_COLOUR * np.dot(a[1], b[1]))


def sharpness(path):
    """Same focus measure the culling uses, so 'sharpest' means one thing."""
    import cv2

    from .quality import _best_region_focus

    img = cv2.imread(str(path))
    if img is None:
        return 0.0
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return _best_region_focus(cv2.resize(grey, (512, 512), interpolation=cv2.INTER_AREA))


def _walk(ordered, sigs, sim_threshold, gap, drift=True):
    """One sequential pass: start a new run when the picture or the clock says so.

    With `drift` the reference follows the run, so a slow pan stays one run.
    Without it, every frame is judged against where the run began - which is
    what breaks up a long stretch shot against a single wall.
    """
    runs, cur, ref = [], [], None
    for (st, ex) in ordered:
        sig = sigs.get(st.base)
        if not cur:
            cur, ref = [(st, ex)], sig
            continue
        too_long = ex["taken"] - cur[-1][1]["taken"] > gap
        alike = similarity(sig, ref) >= sim_threshold if (sig and ref) else False
        if too_long or not alike:
            runs.append(cur)
            cur, ref = [(st, ex)], sig
        else:
            cur.append((st, ex))
            if drift and sig is not None and ref is not None:
                ref = (0.7 * ref[0] + 0.3 * sig[0], 0.7 * ref[1] + 0.3 * sig[1])
    if cur:
        runs.append(cur)
    return runs


def group(pairs, previews_by_stem, sim_threshold=DEFAULT_SIM,
          gap_seconds=DEFAULT_GAP, min_group=MIN_GROUP, max_run=MAX_RUN,
          on_progress=None):
    """pairs: [(stack, exif)] in any order. Returns poses in shooting order."""
    ordered = sorted(pairs, key=lambda t: t[1]["taken"])
    gap = timedelta(seconds=gap_seconds)

    sigs, sharp = {}, {}
    for i, (st, _) in enumerate(ordered, 1):
        p = previews_by_stem.get(st.primary.stem if st.primary else None)
        if p:
            sigs[st.base] = signature(p)
            sharp[st.base] = sharpness(p)
        if on_progress and (i % 25 == 0 or i == len(ordered)):
            on_progress(i, len(ordered))

    poses = _walk(ordered, sigs, sim_threshold, gap)

    # When a whole look was shot against one wall, everything resembles
    # everything and the run swallows the folder - which defeats the point,
    # since the job is to shrink how much you have to look at in one go. Any
    # oversized run is re-walked more strictly until it breaks up or the
    # threshold runs out of room.
    if max_run:
        thr = sim_threshold
        for _ in range(5):
            if all(len(g) <= max_run for g in poses):
                break
            thr = min(0.97, thr + 0.07)
            out = []
            for g in poses:
                if len(g) > max_run:
                    out.extend(_walk(g, sigs, thr, gap, drift=False))
                else:
                    out.append(g)
            if len(out) == len(poses):
                break            # nothing moved, stricter will not help either
            poses = out
        # Anything still oversized is genuinely 40 near-identical frames. Chop
        # it in shooting order rather than break the promise that a folder is
        # small enough to look through in one sitting.
        final = []
        for g in poses:
            if len(g) > max_run:
                for i in range(0, len(g), max_run):
                    final.append(g[i:i + max_run])
            else:
                final.append(g)
        poses = final

    # Merge a stray frame or two into whichever neighbour it resembles more.
    merged = []
    for g in poses:
        if merged and len(g) < min_group:
            merged[-1].extend(g)
        else:
            merged.append(g)

    out = []
    for i, g in enumerate(merged, 1):
        p = Pose(index=i, stacks=[s for s, _ in g])
        p.start, p.end = g[0][1]["taken"], g[-1][1]["taken"]
        p.sharpness = {s.base: sharp.get(s.base, 0.0) for s, _ in g}
        p.best = max(p.stacks, key=lambda s: p.sharpness.get(s.base, 0.0))
        out.append(p)
    return out
