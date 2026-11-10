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


def score_image(path, faces_here, subject_frac=0.25, subject_min=0.0035):
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
    if not faces_here:
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
    if any(r.startswith(("black", "blown")) for r in s.reasons):
        return "reject"
    if "soft" in s.reasons and not math.isnan(s.blur_ratio) \
            and s.blur_ratio < reject_ratio:
        return "reject"
    return "review"


def assess(scores, blur_ratio=0.65, eye_ratio=0.55, eye_abs=None, dark=18.0,
           bright=246.0, blown=0.35, black=0.70, min_face_frac=0.0,
           reject_ratio=0.25, misfocus_ratio=0.5):
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

    for s in scores:
        if "unreadable" in s.reasons:
            continue
        bar = med_face if s.faces else med_no
        s.blur_ratio = (s.sharp / bar) if bar > 0 else float("nan")

        if bar > 0 and s.blur_ratio < blur_ratio:
            s.reasons.append("soft" if s.faces else "soft (no face detected)")
        elif (s.faces and med_pair > 0 and s.global_sharp > 0
              and s.global_sharp >= 0.8 * med_glob
              and (s.face_sharp / s.global_sharp) < misfocus_ratio * med_pair):
            s.reasons.append("focus off the face")

        if (ear_bar is not None and s.eyes_known and s.ear < ear_bar
                and s.main_face_frac >= min_face_frac):
            s.reasons.append("eyes closed")

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
