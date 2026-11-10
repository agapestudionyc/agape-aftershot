"""Work out how many separate photoshoots are sitting in one folder.

Time is the backbone. Cameras stamp every frame, and a real break between
shoots - driving somewhere, a wardrobe change, lunch - leaves a gap that is
obvious in the timestamps, while frames within a shoot come minutes or seconds
apart. So the split is made on capture time, and the other signals are used to
*describe* each cluster rather than to find it: who is in it, what camera, how
long it ran. Describing is what lets a human confirm the split quickly, which
is the point - nothing moves until they do.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from .exif import camera_label, read_exif

DEFAULT_GAP_MIN = 45
MIN_FRAMES = 5
# The size the rest of the program expects a cached preview to be.
P_DEFAULT_LONG_SIDE = 2048


@dataclass
class Shoot:
    index: int
    stacks: list = field(default_factory=list)
    start=None
    end=None
    camera: str = ""
    people: list = field(default_factory=list)
    name: str = ""
    sample: list = field(default_factory=list)   # preview paths for review

    @property
    def count(self):
        return len(self.stacks)

    @property
    def frames(self):
        return [s.frame for s in self.stacks if s.frame is not None]

    @property
    def span(self):
        if not (self.start and self.end):
            return timedelta(0)
        return self.end - self.start

    def span_text(self):
        mins = int(self.span.total_seconds() // 60)
        if mins < 60:
            return f"{mins} min"
        return f"{mins // 60}h {mins % 60:02d}m"

    def suggested_name(self):
        """Andres's convention: date first, then who, then what."""
        date = self.start.strftime("%Y-%m-%d") if self.start else "undated"
        who = self.people[0] if self.people else ""
        if who and not who.startswith("person_"):
            return f"{date} - {who}"
        return f"{date} - shoot {self.index}"


def read_times(stacks, on_progress=None):
    """-> list of (stack, exif dict), skipping anything with no readable time."""
    from .stop import checkpoint

    out = []
    for i, st in enumerate(stacks, 1):
        checkpoint()
        p = st.primary
        if p is None:
            continue
        info = read_exif(p)
        if info.get("taken"):
            out.append((st, info))
        if on_progress and (i % 100 == 0 or i == len(stacks)):
            on_progress(i, len(stacks))
    return out


def split_by_time(pairs, gap_minutes=DEFAULT_GAP_MIN, min_frames=MIN_FRAMES):
    """Cluster frames into shoots wherever the camera went quiet long enough."""
    if not pairs:
        return []
    pairs = sorted(pairs, key=lambda t: t[1]["taken"])
    gap = timedelta(minutes=gap_minutes)

    groups = [[pairs[0]]]
    for prev, cur in zip(pairs, pairs[1:]):
        if cur[1]["taken"] - prev[1]["taken"] > gap:
            groups.append([cur])
        else:
            groups[-1].append(cur)

    # A handful of stray frames is not a shoot; fold them into the run before.
    merged = []
    for g in groups:
        if merged and len(g) < min_frames:
            merged[-1].extend(g)
        else:
            merged.append(g)

    shoots = []
    for i, g in enumerate(merged, 1):
        sh = Shoot(index=i, stacks=[p[0] for p in g])
        sh.start = g[0][1]["taken"]
        sh.end = g[-1][1]["taken"]
        cams = {camera_label(p[1]) for p in g if p[1].get("make") or p[1].get("model")}
        sh.camera = ", ".join(sorted(cams)) if cams else ""
        shoots.append(sh)
    return shoots


def gaps_report(pairs, top=8):
    """The largest quiet periods, so a threshold can be chosen with eyes open."""
    pairs = sorted(pairs, key=lambda t: t[1]["taken"])
    gaps = []
    for prev, cur in zip(pairs, pairs[1:]):
        d = cur[1]["taken"] - prev[1]["taken"]
        if d.total_seconds() > 60:
            gaps.append((d, prev[0], cur[0]))
    gaps.sort(key=lambda t: -t[0].total_seconds())
    return gaps[:top]


def appearance_signatures(stacks, cache_dir, long_side=320, on_progress=None):
    """A small colour fingerprint per frame: outfit, backdrop, light.

    Cheap enough to run over a whole card, and it moves when the thing in front
    of the camera moves - a change of shirt or a change of wall both show up.
    """
    import cv2
    import numpy as np

    from . import previews as P

    # These are 320px scraps, only ever compared against each other. They need a
    # home of their own: left in the main cache they get picked up later as *the*
    # preview for that frame, and the viewer shows you a postage stamp when you
    # are trying to judge focus.
    srcs = [s.primary for s in stacks if s.primary]
    res = P.extract_many(srcs, Path(cache_dir) / "sig", long_side=long_side)
    from .stop import checkpoint

    sigs = {}
    for i, r in enumerate(res, 1):
        checkpoint()
        if not r.ok:
            continue
        img = cv2.imread(str(r.out))
        if img is None:
            continue
        hsv = cv2.cvtColor(cv2.resize(img, (160, 160)), cv2.COLOR_BGR2HSV)
        h = cv2.calcHist([hsv], [0, 1, 2], None, [8, 8, 4],
                         [0, 180, 0, 256, 0, 256])
        h = cv2.normalize(h, h).flatten().astype("float32")
        sigs[r.source.stem] = h
        if on_progress and (i % 100 == 0 or i == len(res)):
            on_progress(i, len(res))
    return sigs


def scene_boundaries(ordered, sigs, window=6, threshold=0.42, min_len=10):
    """Indices where the picture changes and *stays* changed.

    Comparing a block of frames before against a block after, rather than one
    frame against the next, is what separates a real scene change from the
    model simply turning around.
    """
    import cv2
    import numpy as np

    keys = [st.base for st in ordered]
    n = len(keys)
    if n < 2 * min_len:
        return []

    def block(a, b):
        vs = [sigs[k] for k in keys[a:b] if k in sigs]
        return np.median(np.vstack(vs), axis=0).astype("float32") if vs else None

    scores = [0.0] * n
    for i in range(min_len, n - min_len):
        before, after = block(max(0, i - window), i), block(i, min(n, i + window))
        if before is None or after is None:
            continue
        scores[i] = float(cv2.compareHist(before, after, cv2.HISTCMP_BHATTACHARYYA))

    cuts = []
    for i in range(min_len, n - min_len):
        if scores[i] < threshold:
            continue
        lo, hi = max(0, i - min_len), min(n, i + min_len)
        if scores[i] < max(scores[lo:hi]):
            continue                       # not the peak of this transition
        if cuts and i - cuts[-1] < min_len:
            continue
        cuts.append(i)
    return cuts


def split_combined(pairs, sigs, gap_minutes=DEFAULT_GAP_MIN, scene_threshold=0.42,
                   min_frames=MIN_FRAMES, min_len=10):
    """Split on a long silence OR on a sustained change of scene."""
    if not pairs:
        return []
    pairs = sorted(pairs, key=lambda t: t[1]["taken"])
    ordered = [p[0] for p in pairs]
    gap = timedelta(minutes=gap_minutes)

    cuts = set()
    for i in range(1, len(pairs)):
        if pairs[i][1]["taken"] - pairs[i - 1][1]["taken"] > gap:
            cuts.add(i)
    if sigs:
        cuts.update(scene_boundaries(ordered, sigs, threshold=scene_threshold,
                                     min_len=min_len))

    groups, start = [], 0
    for c in sorted(cuts):
        if c - start >= min_frames:
            groups.append(pairs[start:c])
            start = c
    groups.append(pairs[start:])

    shoots = []
    for i, g in enumerate([g for g in groups if g], 1):
        sh = Shoot(index=i, stacks=[p[0] for p in g])
        sh.start, sh.end = g[0][1]["taken"], g[-1][1]["taken"]
        cams = {camera_label(p[1]) for p in g if p[1].get("make") or p[1].get("model")}
        sh.camera = ", ".join(sorted(cams)) if cams else ""
        shoots.append(sh)
    return shoots


def merge_by_people(shoots, cache_dir, gap_minutes=DEFAULT_GAP_MIN, backend=None,
                    per_shoot=14, long_side=P_DEFAULT_LONG_SIDE, on_msg=None,
                    on_progress=None):
    """Fold back together whatever the scene split cut apart by mistake.

    The split above works on colour and light, and a single continuous
    session can move through more of both than it looks like it should - a
    change of pose, a step to a different wall, a client turning under the
    window. None of that ends the session, and there is one signal that says
    so plainly: the same people are still in front of the camera. This is how
    four real photoshoots came back as thirty-three proposed groups - every
    pose change the scene detector mistook for a new shoot stayed a separate
    group forever, because nothing after the split ever looked at who was in
    each half of the cut.

    So every provisional group here is analysed for who is in it - all of
    them at once, in one shared embedding space, so "the same person" means
    the same face cluster everywhere rather than a coincidence of two
    independent runs both landing on "person_01". Two *adjacent* groups,
    separated by less than `gap_minutes` - never across a real break in the
    day, only across a cut the scene detector invented within one - merge
    back into a single shoot when someone who actually recurs is in both.

    A group with nobody recognisable in it - an object, a product, an empty
    room - never merges with anything. There is no person to agree it is the
    same session, which is exactly right: it almost certainly is not.
    """
    from . import faces as F
    from . import previews as P

    if not shoots:
        return shoots
    if backend is None:
        backend = F.get_backend(on_msg=on_msg)

    # One pool, tagged by which provisional group each face came from, so the
    # clustering below happens in one shared space instead of one call per
    # group - the only way "the same person" is a comparable statement at all.
    #
    # This loop is exactly where a run went quiet with no way to tell: every
    # one of thirty-three groups means extracting previews and running face
    # detection on up to fourteen frames each, and none of that printed a
    # single line - the previous version of this had no progress report at
    # all, so a long Separate Projects looked identical whether it was moving
    # or stuck. `on_progress(i, n)` is read by the same "count / total" line
    # every other stage in this program reports through.
    tagged = []
    for si, sh in enumerate(shoots):
        picks = _spread(sh.stacks, per_shoot)
        srcs = [s.primary for s in picks if s.primary]
        res = P.extract_many(srcs, Path(cache_dir), long_side=long_side)
        sh.sample = [r.out for r in res if r.ok]
        if sh.sample:
            found = F.analyse_previews(sh.sample, backend, min_face_frac=0.0035)
            tagged.extend((si, f) for f in found)
        if on_progress:
            on_progress(si + 1, len(shoots))

    people_in = [set() for _ in shoots]
    if tagged:
        groups = F.cluster([f for _, f in tagged], backend_name=backend.name)
        groups = F.name_from_library(groups, backend.name)
        # Only people who actually recur - one frame is a passer-by, the same
        # rule this always used, now applied once instead of per group.
        recurring = {id(g) for g in groups if len(g.images) >= 2}
        owner = {id(f): g for g in groups for f in g.faces}
        for si, f in tagged:
            g = owner.get(id(f))
            if g is not None and id(g) in recurring:
                people_in[si].add(g.name)

    gap = timedelta(minutes=gap_minutes)
    merged = [shoots[0]]
    kept = [people_in[0]]
    for i in range(1, len(shoots)):
        cur, prev = shoots[i], merged[-1]
        if cur.start - prev.end <= gap and (kept[-1] & people_in[i]):
            prev.stacks.extend(cur.stacks)
            prev.end = cur.end
            cams = set(filter(None, prev.camera.split(", "))) | \
                   set(filter(None, cur.camera.split(", ")))
            prev.camera = ", ".join(sorted(cams))
            prev.sample = (prev.sample + cur.sample)[:per_shoot]
            kept[-1] = kept[-1] | people_in[i]
            if on_msg:
                who = ", ".join(sorted(kept[-1])) or "the same person"
                on_msg(f"folded shoot {cur.index} back into {prev.index} "
                      f"- {who} carries on through both")
        else:
            merged.append(cur)
            kept.append(people_in[i])

    for i, sh in enumerate(merged, 1):
        sh.index = i
        sh.people = sorted(kept[i - 1])
    return merged


def _spread(items, n):
    """n items spread evenly across the list, not just the first n."""
    if len(items) <= n:
        return list(items)
    step = len(items) / float(n)
    return [items[int(i * step)] for i in range(n)]
