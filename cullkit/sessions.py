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
    # Why this shoot begins where it does - {"reason": "time" | "subject",
    # "gap_minutes", "text"} - and the pauses inside it that were looked at
    # and deliberately not cut. Empty on the first shoot and on the old path.
    cut: dict = field(default_factory=dict)
    joined: list = field(default_factory=list)

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


# =====================================================================
# Separating by subject (2026-11-11)
# =====================================================================
# Everything above this line cuts where the *picture* changes: a colour
# fingerprint of the whole frame, which is mostly background. So one client
# walking from a white wall to a brick one came back as two shoots, and the
# people pass that follows could only glue some of them together again.
#
# This asks the question the photographer is actually asking - is it still
# the same person in front of the camera - and asks it only where a cut could
# go at all: at a pause. A session is never cut in the middle of continuous
# shooting, whatever the wall behind does.
#
#   pause shorter than `medium`             never a cut
#   pause of `medium` or more, same subject  one session
#   pause of `medium` or more, new subject   a new session   (reason: subject)
#   pause of `big` or more                   a new session   (reason: time)
#
# The old way is still here, behind `cull shoots --by-scene`.
DEFAULT_MEDIUM_MIN = 10
DEFAULT_BIG_MIN = 90
# Without faces to go on, a pause has to speak for itself. This is the
# number the program used for that from the beginning.
DEFAULT_BLIND_MIN = 45
SUBJECT_MIN_FRAC = 0.0035     # a face smaller than this is somebody in the background
SUBJECT_MIN_SCORE = 0.60      # a detection weaker than this is not counted at all
CO_SUBJECT_RATIO = 0.35       # a second face this big next to the largest is a subject too
PER_ATOM = 12                 # frames looked at in each stretch of continuous shooting


def subject_faces(faces, subject_min=SUBJECT_MIN_FRAC, min_score=SUBJECT_MIN_SCORE,
                  co_ratio=CO_SUBJECT_RATIO):
    """The faces in one frame that are who the picture is of.

    The largest confident face, and any other of comparable size - a couple,
    a group. Someone small at the back of the room is not a subject however
    sharp the detector thinks they are, and a weak detection is not anybody.
    """
    ok = [f for f in faces or []
          if f.score >= min_score and f.area_frac >= subject_min]
    if not ok:
        return []
    biggest = max(f.area_frac for f in ok)
    return [f for f in ok if f.area_frac >= co_ratio * biggest]


def _atoms(pairs, medium):
    """Stretches of continuous shooting: split wherever the camera paused."""
    out = [[pairs[0]]]
    for prev, cur in zip(pairs, pairs[1:]):
        if cur[1]["taken"] - prev[1]["taken"] >= medium:
            out.append([cur])
        else:
            out[-1].append(cur)
    return out


def _pause_text(td):
    mins = int(td.total_seconds() // 60)
    if mins < 60:
        return f"{mins} min"
    return f"{mins // 60} h {mins % 60:02d} min" if mins % 60 else f"{mins // 60} h"


def split_by_subject(pairs, faces_by_base, backend_name="insightface",
                     medium_minutes=DEFAULT_MEDIUM_MIN, big_minutes=DEFAULT_BIG_MIN,
                     blind_minutes=DEFAULT_BLIND_MIN, min_frames=MIN_FRAMES,
                     threshold=None):
    """Cut a card into sessions by who is in front of the camera.

    `pairs` is [(stack, exif)]. `faces_by_base` maps a frame's base name to
    the faces found in it, for whichever frames were looked at - it does not
    have to be all of them, and a frame that is missing is simply a frame
    nobody looked at. Pass None when there are no faces to go on at all, and
    the split falls back to pauses alone.

    Returns Shoot objects. Each carries `cut` - why it starts where it does -
    and `joined`, the pauses it was deliberately *not* cut at.
    """
    from collections import Counter

    from . import faces as F

    if not pairs:
        return []
    pairs = sorted(pairs, key=lambda t: t[1]["taken"])
    blind = faces_by_base is None
    medium = timedelta(minutes=blind_minutes if blind else medium_minutes)
    big = timedelta(minutes=min(big_minutes, blind_minutes) if blind else big_minutes)
    atoms = _atoms(pairs, medium)
    gaps = [atoms[i][0][1]["taken"] - atoms[i - 1][-1][1]["taken"]
            for i in range(1, len(atoms))]          # gaps[i-1] sits before atom i

    # ---- who is in each stretch ----
    # Every subject face on the card goes into one clustering, so "the same
    # person" means the same thing in the first stretch and the last.
    pool = []                                       # (atom index, base, face)
    if not blind:
        for ai, atom in enumerate(atoms):
            for st, _ in atom:
                for f in subject_faces(faces_by_base.get(st.base)):
                    pool.append((ai, st.base, f))
    names = {}
    if pool:
        groups = F.cluster([f for _, _, f in pool], threshold=threshold,
                           backend_name=backend_name)
        try:
            groups = F.name_from_library(groups, backend_name)
        except Exception:
            pass
        for g in groups:
            for f in g.faces:
                names[id(f)] = g.name

    subjects = [Counter() for _ in atoms]           # person -> frames they are in
    for ai, atom in enumerate(atoms):
        seen = {}                                   # base -> people in that frame
        for a, base, f in pool:
            if a == ai:
                seen.setdefault(base, set()).add(names[id(f)])
        tally = Counter(p for people in seen.values() for p in people)
        # Sustained, not a single frame: somebody who walks through one
        # picture is not who the stretch is of. A stretch with only a frame
        # or two looked at has to take what it can get.
        need = 2 if len(seen) >= 3 else 1
        subjects[ai] = Counter({p: n for p, n in tally.items() if n >= need})

    # ---- where to cut ----
    cuts = {}                                       # atom index it starts at -> why
    joined = {}                                     # atom index -> note on a pause kept
    blocks, start = [], 0
    for i, g in enumerate(gaps, 1):
        if g >= big:
            cuts[i] = {"reason": "time", "gap_minutes": int(g.total_seconds() // 60),
                       "text": f"{_pause_text(g)} with no photos"}
            blocks.append((start, i))
            start = i
    blocks.append((start, len(atoms)))

    for lo, hi in blocks:
        faced = [i for i in range(lo, hi) if subjects[i]]
        if not faced:
            continue                                # nobody to tell apart: one session
        session = Counter(subjects[faced[0]])
        for p, q in zip(faced, faced[1:]):
            mine, theirs = subjects[q], session
            main_here = mine.most_common(1)[0][0]
            main_there = theirs.most_common(1)[0][0]
            same = main_here in theirs or main_there in mine
            # The widest pause between the two is where a cut would go. Any
            # stretch with nobody in it - a detail, the room - stays with
            # whichever side it is closer to in time.
            at = max(range(p + 1, q + 1), key=lambda i: gaps[i - 1])
            g = gaps[at - 1]
            if same:
                session.update(mine)
                who = main_here if not main_here.startswith("person_") else "the same person"
                joined[at] = (f"kept together across a {_pause_text(g)} pause"
                              f" - {who} carries on")
            else:
                cuts[at] = {"reason": "subject",
                            "gap_minutes": int(g.total_seconds() // 60),
                            "text": f"a different person after a {_pause_text(g)} pause"}
                session = Counter(mine)

    # ---- build ----
    bounds = sorted(cuts)
    spans = list(zip([0] + bounds, bounds + [len(atoms)]))
    groups = []
    for a, b in spans:
        frames = [pr for atom in atoms[a:b] for pr in atom]
        who = Counter()
        for i in range(a, b):
            who.update(subjects[i])
        notes = [joined[i] for i in range(a + 1, b) if i in joined]
        groups.append({"frames": frames, "who": who, "cut": cuts.get(a, {}),
                       "joined": notes})

    # A handful of stray frames is not a shoot; fold them into the run before.
    merged = []
    for g in groups:
        if merged and len(g["frames"]) < min_frames:
            merged[-1]["frames"].extend(g["frames"])
            merged[-1]["who"].update(g["who"])
            merged[-1]["joined"].extend(g["joined"])
        else:
            merged.append(g)

    shoots = []
    for i, g in enumerate(merged, 1):
        fr = g["frames"]
        sh = Shoot(index=i, stacks=[p[0] for p in fr])
        sh.start, sh.end = fr[0][1]["taken"], fr[-1][1]["taken"]
        cams = {camera_label(p[1]) for p in fr if p[1].get("make") or p[1].get("model")}
        sh.camera = ", ".join(sorted(cams)) if cams else ""
        sh.people = [p for p, _ in g["who"].most_common()]
        sh.cut = dict(g["cut"]) if i > 1 else {}
        sh.joined = list(g["joined"])
        shoots.append(sh)
    return shoots


def sample_for_identity(pairs, medium_minutes=DEFAULT_MEDIUM_MIN, per_atom=PER_ATOM):
    """The frames worth looking at to know who each stretch is of.

    A dozen spread across every stretch of continuous shooting, however long
    it is. A three-thousand-frame card is still only a few hundred frames to
    look at, which on a small laptop is the difference between a minute and
    the length of a lunch.
    """
    if not pairs:
        return []
    pairs = sorted(pairs, key=lambda t: t[1]["taken"])
    out = []
    for atom in _atoms(pairs, timedelta(minutes=medium_minutes)):
        out.extend(st for st, _ in _spread(atom, per_atom))
    return out


def find_subjects(stacks, cache_dir, backend=None, long_side=P_DEFAULT_LONG_SIDE,
                  on_msg=None, on_progress=None):
    """Look at these frames and say who is in each. -> ({base: [Face]}, backend name)

    Reads the main preview cache at its one standard size, like everything
    else that looks at faces; nothing here writes a smaller picture anywhere.
    """
    from . import faces as F
    from . import previews as P

    if backend is None:
        backend = F.get_backend(on_msg=on_msg)
    srcs = [s.primary for s in stacks if s.primary]
    res = P.extract_many(srcs, Path(cache_dir), long_side=long_side)
    paths = [r.out for r in res if r.ok]
    state = {"n": 0}

    def step(_path, _n):
        state["n"] += 1
        if on_progress and (state["n"] % 10 == 0 or state["n"] == len(paths)):
            on_progress(state["n"], len(paths))

    found = F.analyse_previews(paths, backend, on_step=step)
    by_base = {Path(p).stem: [] for p in paths}
    for f in found:
        by_base.setdefault(Path(f.image).stem, []).append(f)
    return by_base, backend.name


def _spread(items, n):
    """n items spread evenly across the list, not just the first n."""
    if len(items) <= n:
        return list(items)
    step = len(items) / float(n)
    return [items[int(i * step)] for i in range(n)]
