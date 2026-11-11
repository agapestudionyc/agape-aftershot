"""Score every frame for the things that actually make a photographer bin it.

Three checks, and one design decision behind all of them.

**Sharpness is measured on the face, never on the whole frame.** A global focus
measure marks a shallow-depth-of-field portrait as blurry, because most of the
frame genuinely is blurry - that is the look. Scoring the face crop instead
asks the question that matters: did the focus land on the person. Frames with
no face fall back to a global measure.

**Thresholds are relative to this shoot.** Absolute focus numbers mean nothing
across lenses, apertures and light. A frame is called soft when it is markedly
softer than the median frame of the same shoot, so a soft-focus set does not
get wholly condemned and a crisp set does not hide its misses.

**Eye openness** comes from the 106-point face mesh: the eye contour points are
found by proximity to the detector's eye keypoints rather than by hard-coded
indices, and are de-rotated by head roll first, so tilted heads measure right.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

CANON_FACE = 192  # every face crop is measured at this size, so sizes compare


@dataclass
class Score:
    image: Path
    base: str = ""
    faces: int = 0        # faces large enough to be the subject
    detected: int = 0     # every face found, subject-sized or not
    tier: str = "keep"    # keep | review | reject
    face_sharp: float = 0.0
    global_sharp: float = 0.0
    main_face_frac: float = 0.0
    ear: float = float("nan")       # eye aspect ratio, lower = more closed
    eyes_known: bool = False
    mean_luma: float = 0.0
    clip_white: float = 0.0
    clip_black: float = 0.0
    blur_ratio: float = float("nan")  # sharpness / shoot median
    focus: tuple = (0.5, 0.5)  # where the judged sharpness was measured, 0-1
    reasons: list = field(default_factory=list)
    eye_sharp: float = 0.0     # sharpness of the strip across the eyes
    face_score: float = 0.0    # how sure the detector was of the judged face
    eyes_why: str = ""         # when the eyes cannot be judged, the reason

    @property
    def sharp(self) -> float:
        return self.face_sharp if self.faces else self.global_sharp

    @property
    def rejected(self) -> bool:
        return bool(self.reasons)

    @property
    def verdict(self) -> str:
        return ", ".join(self.reasons) if self.reasons else "keep"


def _focus(gray) -> float:
    """Laplacian variance normalised by contrast.

    Dividing by the variance of the crop keeps a dark or low-contrast face from
    reading as soft purely because it carries less signal.
    """
    import cv2

    lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    var = float(np.var(gray))
    return float(np.var(lap)) / (var + 1e-6)


def _best_region_focus(gray, grid=6, pct=90) -> float:
    """Sharpness of the sharpest part of the frame, not of the whole frame.

    For a frame with no face, the whole-frame measure asks the wrong question.
    A subject against a distant background is mostly out-of-focus pixels by
    area, so averaging over the frame condemns a perfectly good shot. Scoring
    tiles and taking a high percentile asks instead whether *anything* in the
    frame is sharp, which is what "did this one work" actually means.

    The percentile rather than the max keeps a single specular highlight or a
    speck of noise from carrying the whole frame.
    """
    h, w = gray.shape[:2]
    th, tw = h // grid, w // grid
    if th < 8 or tw < 8:
        return _focus(gray)
    vals = []
    for r in range(grid):
        for c in range(grid):
            tile = gray[r * th:(r + 1) * th, c * tw:(c + 1) * tw]
            if tile.size:
                vals.append(_focus(tile))
    if not vals:
        return _focus(gray)
    return float(np.percentile(vals, pct))


def sharpest_point(gray, grid=8) -> tuple:
    """Centre of the sharpest tile, as fractions of width and height.

    This is where the zoom panel opens on a frame with no face, so you are
    shown the part the measure believed was in focus rather than the middle.

    Unlike the score, this uses raw Laplacian energy, not the
    contrast-normalised kind. Normalising is right for judging a face, but for
    *finding* the in-focus spot it hands the prize to a near-black patch of
    background, where sensor noise divided by almost no contrast is huge.
    """
    import cv2

    h, w = gray.shape[:2]
    th, tw = h // grid, w // grid
    if th < 8 or tw < 8:
        return (0.5, 0.5)
    best, at = -1.0, (0.5, 0.5)
    for r in range(grid):
        for c in range(grid):
            tile = gray[r * th:(r + 1) * th, c * tw:(c + 1) * tw]
            v = float(np.var(cv2.Laplacian(tile, cv2.CV_32F, ksize=3)))
            if v > best:
                best, at = v, ((c + 0.5) / grid, (r + 0.5) / grid)
    return at


def look_point(gray, boxes=(), subject_min=0.0035, grid=8):
    """Where the zoom panel should look on a frame Sift has not judged yet, and
    what it found there: ``((x, y), kind)`` with kind "face", "subject" or "scene".

    The same order a photographer checks focus in: the face if there is one big
    enough to be the subject, then the subject - the sharp thing standing out
    from a soft background, favouring the middle of the frame where subjects
    usually are - and only when nothing stands out, the sharpest spot of the
    scene. `boxes` are face boxes (x, y, w, h) in `gray`'s own pixels.
    """
    import cv2

    h, w = gray.shape[:2]
    faces = [b for b in boxes if (b[2] * b[3]) / float(w * h) >= subject_min]
    if faces:
        x, y, bw, bh = max(faces, key=lambda b: b[2] * b[3])
        # Between the eyes, like Sift, not the middle of the box (the nose).
        return ((min(1.0, max(0.0, (x + bw / 2) / w)),
                 min(1.0, max(0.0, (y + bh * 0.4) / h))), "face")

    th, tw = h // grid, w // grid
    if th < 8 or tw < 8:
        return ((0.5, 0.5), "scene")
    energy = np.zeros((grid, grid), dtype=np.float64)
    for r in range(grid):
        for c in range(grid):
            tile = gray[r * th:(r + 1) * th, c * tw:(c + 1) * tw]
            energy[r, c] = float(np.var(cv2.Laplacian(tile, cv2.CV_32F, ksize=3)))
    med = float(np.median(energy)) or 1e-6
    yy, xx = np.mgrid[0:grid, 0:grid]
    off = np.hypot((xx + 0.5) / grid - 0.5, (yy + 0.5) / grid - 0.5) / 0.7071
    weighted = energy * (1.0 - 0.55 * off)
    r, c = np.unravel_index(int(np.argmax(weighted)), weighted.shape)
    # A subject is something clearly sharper than most of the frame. A frame
    # that is sharp all over (a landscape, a flat-lit room) has no such thing,
    # so it is read as a scene and the zoom goes to its sharpest spot.
    if energy[r, c] >= 3.0 * med:
        return (((c + 0.5) / grid, (r + 0.5) / grid), "subject")
    return (sharpest_point(gray, grid), "scene")


def _face_focus(img, bbox, pad=0.12) -> float:
    import cv2

    h, w = img.shape[:2]
    x, y, bw, bh = bbox
    px, py = bw * pad, bh * pad
    x0, y0 = max(0, int(x - px)), max(0, int(y - py))
    x1, y1 = min(w, int(x + bw + px)), min(h, int(y + bh + py))
    crop = img[y0:y1, x0:x1]
    if crop.size == 0 or min(crop.shape[:2]) < 12:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # Canonical size so a big face and a small one are judged on equal terms.
    interp = cv2.INTER_AREA if gray.shape[0] > CANON_FACE else cv2.INTER_CUBIC
    gray = cv2.resize(gray, (CANON_FACE, CANON_FACE), interpolation=interp)
    return _focus(gray)


def _frontal(face) -> bool:
    """Eye measurements are only trustworthy on a roughly front-facing head."""
    kps = face.kps
    if kps is None or len(kps) < 3:
        return False
    le, re_, nose = kps[0], kps[1], kps[2]
    iod = float(np.linalg.norm(re_ - le))
    if iod <= 1:
        return False
    dl = float(np.linalg.norm(nose - le))
    dr = float(np.linalg.norm(nose - re_))
    return abs(dl - dr) / iod < 0.38


# Eye contour rings in insightface's 106-point mesh, established by probing the
# model rather than trusted from documentation. Each eye is a 10-point ring; the
# two points sitting dead centre are the pupil, not the lid, so they are left
# out. Indices 43-51 and 97-105 are the eyebrows and must not be included - a
# proximity search around the eye grabs them and reports a near-circular blob,
# which makes every eye look wide open.
_LEFT_EYE = (33, 35, 36, 37, 39, 40, 41, 42)
_RIGHT_EYE = (87, 89, 90, 91, 93, 94, 95, 96)


def eye_aspect_ratio(face) -> float:
    """Height/width of the narrower eye. Falls as the lid closes.

    Points are de-rotated by head roll first, so a tilted head measures true.
    The ring's centroid is checked against the detector's own eye keypoint, so
    if the mesh ever changes ordering this returns unknown rather than a
    confidently wrong number.
    """
    lm = face.landmarks
    kps = face.kps
    if lm is None or len(lm) < 106 or kps is None or len(kps) < 2:
        return float("nan")
    le, re_ = kps[0], kps[1]
    iod = float(np.linalg.norm(re_ - le))
    if iod <= 4:
        return float("nan")

    ang = math.atan2(float(re_[1] - le[1]), float(re_[0] - le[0]))
    c, s = math.cos(-ang), math.sin(-ang)
    rot = np.array([[c, -s], [s, c]], dtype=np.float32)

    ratios = []
    for ring in (_LEFT_EYE, _RIGHT_EYE):
        pts = lm[list(ring)]
        centre = pts.mean(axis=0)
        if min(float(np.linalg.norm(centre - le)),
               float(np.linalg.norm(centre - re_))) > 0.25 * iod:
            continue  # ring is not where an eye should be; do not guess
        p = (pts - centre) @ rot.T
        w = float(p[:, 0].max() - p[:, 0].min())
        h = float(p[:, 1].max() - p[:, 1].min())
        if w > 1e-6:
            ratios.append(h / w)
    if not ratios:
        return float("nan")
    return float(min(ratios))


def _exposure(img):
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    total = gray.size
    return (
        float(gray.mean()),
        float((gray > 250).sum()) / total,
        float((gray < 5).sum()) / total,
    )


def score_image(path, faces_here, subject_frac=0.25, subject_min=0.0035,
                min_face_score=0.60):
    """Score one preview. `faces_here` are the faces already found in it.

    `subject_min` is the fraction of the frame a face must fill to count as the
    subject. Measured on real shoots: subject faces in a fashion set ran
    0.40-3.3% of frame, while background bystanders on a location shoot ran
    0.09-1.28%, median 0.17%.
    """
    import cv2

    path = Path(path)
    sc = Score(image=path, base=path.stem)
    img = cv2.imread(str(path))
    if img is None:
        sc.reasons.append("unreadable")
        return sc

    h, w = img.shape[:2]
    sc.mean_luma, sc.clip_white, sc.clip_black = _exposure(img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (512, 512), interpolation=cv2.INTER_AREA)
    sc.global_sharp = _best_region_focus(small)
    sc.focus = sharpest_point(small)

    faces_here = list(faces_here or [])
    sc.detected = len(faces_here)
    # A weak detection is a pattern in a curtain as often as it is a person,
    # and judging focus on it condemns a good frame for the curtain's sake.
    faces_here = [f for f in faces_here if f.score >= min_face_score]
    if not faces_here:
        sc.eyes_why = "no face found"
        return sc

    faces_here.sort(key=lambda f: -(f.bbox[2] * f.bbox[3]))
    # A detected face is not automatically the subject. On a shoot whose subject
    # is not a person, the only faces found are bystanders in the background -
    # small, far away and out of focus. Anchoring sharpness to those judges the
    # wrong thing entirely, so anything below the subject size is ignored and
    # the frame is scored on its sharpest region instead.
    faces_here = [f for f in faces_here if f.area_frac >= subject_min]
    sc.faces = len(faces_here)
    if not faces_here:
        sc.eyes_why = "only small faces in the background"
        return sc

    biggest = faces_here[0].bbox[2] * faces_here[0].bbox[3]
    # Subjects are the faces comparable in size to the largest one. A guest
    # blinking in the background must not condemn the frame.
    subjects = [
        f for f in faces_here
        if (f.bbox[2] * f.bbox[3]) >= subject_frac * biggest
    ]

    sc.main_face_frac = faces_here[0].area_frac
    sc.face_sharp, judged = max(
        ((_face_focus(img, f.bbox), f) for f in subjects), key=lambda t: t[0])
    sc.face_score = float(judged.score)
    sc.eye_sharp = _eye_focus(img, judged)
    x, y, bw, bh = judged.bbox
    # Between the eyes rather than the middle of the box, which is the nose.
    sc.focus = (min(1.0, max(0.0, (x + bw / 2) / w)),
                min(1.0, max(0.0, (y + bh * 0.4) / h)))

    ears = []
    for f in subjects:
        if not _frontal(f):
            continue
        e = eye_aspect_ratio(f)
        if not math.isnan(e):
            ears.append(e)
    if ears:
        sc.ear = float(min(ears))  # the worst set of eyes among the subjects
        sc.eyes_known = True
    elif not any(_frontal(f) for f in subjects):
        sc.eyes_why = "the face is turned away"
    elif all(getattr(f, "landmarks", None) is None for f in subjects):
        sc.eyes_why = "this face model cannot measure eyes"
    else:
        sc.eyes_why = "the eyes are too small or hidden to measure"
    return sc


def _tier(s, reject_ratio):
    """Sort a flagged frame into 'bin it' versus 'you decide'.

    Only the unarguable failures are binned: a frame that is black, blown, or
    so far below the shoot's sharpness that no crop could save it. Everything
    else is a judgement call and goes to review - borderline softness, and
    every closed-eye call, because on a real shoot the one frame flagged for
    shut eyes turned out to be a deliberate pose worth keeping.
    """
    if not s.reasons:
        return "keep"
    # A frame the program could not open is a fault in the program, the cache or
    # the drive - never a verdict on the photograph. It gets its own tier so it
    # is reported loudly and moved nowhere.
    if any(r.startswith("unreadable") for r in s.reasons):
        return "error"
    if any(r.startswith(("black", "blown", "misfire")) for r in s.reasons):
        return "reject"
    if "soft" in s.reasons and not math.isnan(s.blur_ratio) \
            and s.blur_ratio < reject_ratio:
        return "reject"
    return "review"


def assess(scores, blur_ratio=0.65, eye_ratio=0.55, eye_abs=None, dark=18.0,
           bright=246.0, blown=0.35, black=0.70, min_face_frac=0.0,
           reject_ratio=0.25, misfocus_ratio=0.5, eye_soft=0.35):
    """Turn raw scores into verdicts, using this shoot's own medians as the bar.

    Both the sharpness and the eye test are relative. Most frames in a shoot
    have open eyes, so the median eye ratio *is* "open" for these faces, this
    lens and this crop, and a blink sits far below it. That self-calibrates in
    a way no absolute number can.
    """
    withface = [s for s in scores if s.faces]
    noface = [s for s in scores if not s.faces]
    # Face sharpness and best-region sharpness are different measurements on
    # different scales, so each frame is judged against its own kind. Mixing
    # them into one median would push every no-face frame to one side of it.
    med_face = float(np.median([s.sharp for s in withface])) if withface else 0.0
    med_no = float(np.median([s.sharp for s in noface])) if noface else 0.0
    if len(withface) < 5:
        med_face = med_no or med_face
    if len(noface) < 5:
        med_no = med_face or med_no
    med = med_face or med_no

    eyes = [s.ear for s in scores if s.eyes_known and not math.isnan(s.ear)]
    med_ear = float(np.median(eyes)) if eyes else float("nan")
    ear_bar = eye_abs if eye_abs is not None else (
        med_ear * eye_ratio if not math.isnan(med_ear) else None
    )

    # Focus that landed somewhere other than the face. The face can still pass
    # the shoot-relative bar while the shoulder or the wall behind is the
    # sharpest thing in the frame. How much sharper the face is than the
    # frame's sharpest region is steady across a shoot when focus is on the
    # eyes, so a frame far below the shoot's usual face-to-frame ratio, whose
    # frame is not itself soft, had its focus somewhere else.
    pairs = [s.face_sharp / s.global_sharp for s in withface if s.global_sharp > 0]
    med_pair = float(np.median(pairs)) if len(pairs) >= 5 else 0.0
    med_glob = float(np.median([s.global_sharp for s in withface])) if withface else 0.0

    eye_vals = [s.eye_sharp for s in withface if getattr(s, "eye_sharp", 0) > 0]
    med_eye = float(np.median(eye_vals)) if len(eye_vals) >= 5 else 0.0

    for s in scores:
        if "unreadable" in s.reasons:
            continue
        bar = med_face if s.faces else med_no
        s.blur_ratio = (s.sharp / bar) if bar > 0 else float("nan")

        if bar > 0 and s.blur_ratio < blur_ratio:
            s.reasons.append("soft" if s.faces else "soft (no face detected)")
            # Why it is soft, because the two are different mistakes. If
            # something else in the frame is as sharp as usual, the focus
            # landed there and missed the face. If nothing is, the camera or
            # the subject moved. (A sharp face against a soft background is
            # neither: that is the picture working, and it never gets here.)
            if s.faces:
                if med_glob > 0 and s.global_sharp >= 0.8 * med_glob:
                    s.reasons.append("missed focus")
                else:
                    s.reasons.append("motion blur")
            elif s.blur_ratio < reject_ratio:
                # No subject and nothing sharp anywhere: the shutter went off
                # at the floor, or while the camera was being swung round.
                s.reasons.append("misfire (nothing in focus)")
        elif (s.faces and med_pair > 0 and s.global_sharp > 0
              and s.global_sharp >= 0.8 * med_glob
              and (s.face_sharp / s.global_sharp) < misfocus_ratio * med_pair):
            s.reasons.append("focus off the face")

        if (ear_bar is not None and s.eyes_known and s.ear < ear_bar
                and s.main_face_frac >= min_face_frac):
            s.reasons.append("eyes closed")
        # The face holds up but the eyes are behind it. Only said of a frame
        # not already called soft - it would be the same complaint twice.
        # Only where the eyes could be read at all (a face turned away or
        # looking down has no eye strip worth measuring), and only when they
        # are behind the face they belong to, not merely in flat light.
        if (s.faces and med_eye > 0 and getattr(s, "eye_sharp", 0) > 0
                and s.eyes_known
                and "soft" not in s.reasons and "eyes closed" not in s.reasons
                and s.eye_sharp < eye_soft * med_eye
                and s.eye_sharp < 0.5 * s.face_sharp):
            s.reasons.append("eyes soft")

        # Clipping alone does not condemn a frame. A white subject against a
        # bright overcast sky legitimately blows out most of the frame and is
        # still a good photograph - measured on a real shoot, frames clipping
        # 80% of pixels held plenty of detail while frames clipping only 34%
        # were mush. So heavy clipping is a reject only when the detail has
        # gone with it. A frame whose average brightness is at either extreme
        # is dead on its own terms and still fails outright.
        gone = bar > 0 and not math.isnan(s.blur_ratio) and s.blur_ratio < blur_ratio
        if (s.mean_luma < dark or s.clip_black > black) and gone:
            s.reasons.append("black / no detail left")
        elif (s.mean_luma > bright or s.clip_white > blown) and gone:
            s.reasons.append("blown / no detail left")
    for s in scores:
        s.tier = _tier(s, reject_ratio)

    return {
        "median_sharpness": med, "with_faces": len(withface),
        "median_face": med_face, "median_noface": med_no,
        "total": len(scores), "median_ear": med_ear, "ear_bar": ear_bar,
    }


# =====================================================================
# Analyse once, judge as often as you like (2026-11-11)
# =====================================================================
# Sifting used to be one act: look at every frame, decide, write the decision
# down. Moving a threshold meant looking at every frame again - minutes of
# face detection to learn what a slider would have told you in a second.
#
# Now the looking and the deciding are two things. Looking writes what was
# *measured* to `_cullkit/scores.json`; deciding (`judge`) reads only that,
# so it is instant and can be done again for every position of a slider.
SCORES_FILE = "scores.json"
OVERRIDES_FILE = "sift_overrides.json"
SCORES_VERSION = 2
MIN_FACE_SCORE = 0.60        # a detection weaker than this is not judged as a face

SETTINGS = {
    "blur_ratio": 0.65,      # softer than this x the shoot's median -> flagged
    "eye_ratio": 0.55,       # eyes narrower than this x the median -> "eyes closed"
    "eye_soft": 0.35,        # eyes softer than this x the median -> "eyes soft"
    "reject_ratio": 0.25,    # below this x the median nothing can save it
    "blown": 0.35, "black": 0.70,
}
_RAW = ("base", "faces", "detected", "face_sharp", "global_sharp", "eye_sharp",
        "main_face_frac", "face_score", "ear", "eyes_known", "eyes_why",
        "mean_luma", "clip_white", "clip_black")


def _eye_focus(img, face) -> float:
    """Sharpness of the strip across both eyes - where focus is meant to land.

    A face can pass on its skin and hair while the eyes are a touch behind;
    this is the measurement that sees it.
    """
    import cv2

    kps = getattr(face, "kps", None)
    if kps is None or len(kps) < 2:
        return 0.0
    le, re_ = kps[0], kps[1]
    iod = float(np.linalg.norm(re_ - le))
    if iod < 12:
        return 0.0
    h, w = img.shape[:2]
    cx, cy = (le[0] + re_[0]) / 2.0, (le[1] + re_[1]) / 2.0
    x0, x1 = int(max(0, cx - 0.85 * iod)), int(min(w, cx + 0.85 * iod))
    y0, y1 = int(max(0, cy - 0.28 * iod)), int(min(h, cy + 0.28 * iod))
    crop = img[y0:y1, x0:x1]
    if crop.size == 0 or min(crop.shape[:2]) < 6:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (160, 52), interpolation=cv2.INTER_AREA
                      if gray.shape[1] > 160 else cv2.INTER_CUBIC)
    return _focus(gray)


def to_row(s) -> dict:
    """What was measured on one frame, and nothing that was decided about it."""
    row = {k: getattr(s, k) for k in _RAW}
    row["ear"] = None if (not s.eyes_known or math.isnan(s.ear)) else round(float(s.ear), 4)
    row["focus"] = [round(float(s.focus[0]), 4), round(float(s.focus[1]), 4)]
    for k in ("face_sharp", "global_sharp", "eye_sharp", "mean_luma"):
        row[k] = round(float(row[k]), 4)
    for k in ("main_face_frac", "face_score", "clip_white", "clip_black"):
        row[k] = round(float(row[k]), 5)
    row["unreadable"] = "unreadable" in s.reasons
    return row


def from_row(row, image_dir=None):
    """A Score with its measurements back, ready to be judged afresh."""
    base = row["base"]
    s = Score(image=(Path(image_dir) / f"{base}.jpg") if image_dir else Path(base),
              base=base)
    for k in _RAW:
        if k in row and k not in ("base", "ear"):
            setattr(s, k, row[k])
    s.ear = float("nan") if row.get("ear") is None else float(row["ear"])
    s.eyes_known = bool(row.get("eyes_known")) and row.get("ear") is not None
    f = row.get("focus") or (0.5, 0.5)
    s.focus = (float(f[0]), float(f[1]))
    if row.get("unreadable"):
        s.reasons.append("unreadable")
    s.group = row.get("group")
    return s


def judge(rows, settings=None, overrides=None, image_dir=None):
    """Decide about every frame from what was measured. -> (scores, stats)

    Nothing here opens a photograph. `overrides` is {base: "keep" | "reject"},
    the photographer's own calls, and they win over everything.
    """
    cfg = dict(SETTINGS)
    cfg.update({k: v for k, v in (settings or {}).items() if v is not None})
    scores = [from_row(r, image_dir) for r in rows]
    stats = assess(scores, blur_ratio=cfg["blur_ratio"], eye_ratio=cfg["eye_ratio"],
                   reject_ratio=cfg["reject_ratio"], blown=cfg["blown"],
                   black=cfg["black"], eye_soft=cfg["eye_soft"],
                   eye_abs=cfg.get("eye_abs"),
                   min_face_frac=cfg.get("min_face_frac") or 0.0)
    for s in scores:
        s.override = ""
        mine = (overrides or {}).get(s.base)
        if mine in ("keep", "reject") and s.tier != "error":
            s.override = mine
            s.tier = mine
    mark_best(scores)
    stats["settings"] = cfg
    return scores, stats


def mark_best(scores) -> None:
    """In every run of near-identical frames, say which one to look at first.

    Not the sharpest of them: the best of them. A frame Sift would keep beats
    one it would not, open eyes beat shut ones, and only then does sharpness
    decide - which is the order a photographer goes through a burst in.
    """
    rank = {"keep": 3, "review": 2, "reject": 1, "error": 0}
    groups = {}
    for s in scores:
        s.best = False
        g = getattr(s, "group", None)
        if g is not None:
            groups.setdefault(g, []).append(s)
    for members in groups.values():
        if len(members) < 2:
            continue
        top = max(members, key=lambda s: (
            rank.get(s.tier, 0),
            0 if "eyes closed" in s.reasons else 1,
            0.0 if math.isnan(s.blur_ratio) else s.blur_ratio))
        top.best = True


def eyes_state(s) -> str:
    """'open', 'closed' or 'unknown' - and unknown is an answer, not a pass."""
    if not s.eyes_known or math.isnan(s.ear):
        return "unknown"
    return "closed" if "eyes closed" in s.reasons else "open"


def frame_out(s) -> dict:
    """One judged frame, as the screen and the record want it."""
    nan = math.isnan(s.blur_ratio)
    return {
        "base": s.base, "tier": s.tier, "reasons": list(s.reasons),
        "verdict": s.verdict, "blur_ratio": None if nan else round(s.blur_ratio, 4),
        "sharpness": round(float(s.sharp), 4), "faces": s.faces, "detected": s.detected,
        "main_face_frac": s.main_face_frac, "face_score": getattr(s, "face_score", 0.0),
        "ear": None if (not s.eyes_known or math.isnan(s.ear)) else round(float(s.ear), 4),
        "eyes": eyes_state(s), "eyes_why": getattr(s, "eyes_why", ""),
        "mean_luma": s.mean_luma, "clip_white": s.clip_white, "clip_black": s.clip_black,
        "face_sharp": s.face_sharp, "global_sharp": s.global_sharp,
        "eye_sharp": getattr(s, "eye_sharp", 0.0),
        "focus": [round(s.focus[0], 4), round(s.focus[1], 4)],
        "group": getattr(s, "group", None), "best": bool(getattr(s, "best", False)),
        "override": getattr(s, "override", ""),
    }


def load_scores(folder):
    """The saved measurements for a shoot, or None if it has not been looked at."""
    import json

    f = Path(folder) / "_cullkit" / SCORES_FILE
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("version") != SCORES_VERSION or not isinstance(data.get("frames"), list):
        return None
    return data


def load_overrides(folder) -> dict:
    import json

    try:
        got = json.loads((Path(folder) / "_cullkit" / OVERRIDES_FILE)
                         .read_text(encoding="utf-8"))
        return {k: v for k, v in got.items() if v in ("keep", "reject")}
    except (OSError, ValueError, AttributeError):
        return {}


def save_override(folder, base, tier) -> dict:
    """Record the photographer's own call on one frame; None takes it back."""
    import json

    cur = load_overrides(folder)
    if tier in ("keep", "reject"):
        cur[str(base)] = tier
    else:
        cur.pop(str(base), None)
    d = Path(folder) / "_cullkit"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (OVERRIDES_FILE + ".tmp")
    tmp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
    tmp.replace(d / OVERRIDES_FILE)
    return cur


def distribution(scores, buckets=10):
    """A crude histogram of blur_ratio, so the threshold can be eyeballed."""
    vals = [s.blur_ratio for s in scores if not math.isnan(s.blur_ratio)]
    if not vals:
        return []
    lo, hi = 0.0, max(2.0, max(vals))
    step = (hi - lo) / buckets
    rows = []
    for i in range(buckets):
        a, b = lo + i * step, lo + (i + 1) * step
        n = sum(1 for v in vals if a <= v < b or (i == buckets - 1 and v >= b))
        rows.append((a, b, n))
    return rows
