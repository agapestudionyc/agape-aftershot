"""Find people in the extracted previews and group the shoot by who is in it.

Two backends, picked automatically:
  insightface  - buffalo_l, the most accurate option, used when it imports
  opencv       - YuNet detector + SFace recogniser, ~40 MB of ONNX, no compiler

Both produce L2-normalised embeddings, so everything downstream (clustering,
the named-person library) is backend-agnostic apart from the match threshold.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MODEL_DIR = Path.home() / ".cullkit" / "models"
LIBRARY = Path.home() / ".cullkit" / "people" / "library.json"

_ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
_YUNET = ("face_detection_yunet_2023mar.onnx",
          f"{_ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx")
_SFACE = ("face_recognition_sface_2021dec.onnx",
          f"{_ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx")

# Cosine *distance* below which two faces are the same person.
THRESHOLDS = {"insightface": 0.55, "opencv": 0.62}


@dataclass
class Face:
    image: Path
    bbox: tuple          # x, y, w, h in preview pixels
    score: float
    embedding: np.ndarray
    area_frac: float = 0.0
    person: str | None = None
    cluster: int = -1
    kps: np.ndarray | None = None        # 5 points: eyes, nose, mouth corners
    landmarks: np.ndarray | None = None  # 106 points, insightface only

    @property
    def frame(self):
        return self.image.stem


@dataclass
class PersonGroup:
    cluster: int
    name: str
    faces: list = field(default_factory=list)

    @property
    def images(self):
        seen = {}
        for f in self.faces:
            seen.setdefault(f.image, None)
        return list(seen)

    def centroid(self):
        v = np.mean([f.embedding for f in self.faces], axis=0)
        n = np.linalg.norm(v)
        return v / n if n else v

    def best_face(self):
        return max(self.faces, key=lambda f: (f.area_frac, f.score))


def _download(name, url, on_msg=None):
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    dest = MODEL_DIR / name
    if dest.exists() and dest.stat().st_size > 100_000:
        return dest
    if on_msg:
        on_msg(f"downloading model {name} ...")
    tmp = dest.with_suffix(".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)
    return dest


class _InsightBackend:
    name = "insightface"

    def __init__(self, det_size=640, on_msg=None):
        import contextlib
        import io as _io
        import warnings

        warnings.filterwarnings("ignore", category=FutureWarning)
        if on_msg:
            on_msg("loading buffalo_l (first run downloads it, ~300 MB) ...")
        # The loader chatters about every ONNX file it opens; keep the run readable.
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            from insightface.app import FaceAnalysis

            self.app = FaceAnalysis(
                name="buffalo_l", providers=["CPUExecutionProvider"]
            )
            self.app.prepare(ctx_id=-1, det_size=(det_size, det_size))

    def analyse(self, bgr, path):
        out = []
        h, w = bgr.shape[:2]
        for f in self.app.get(bgr):
            x1, y1, x2, y2 = [float(v) for v in f.bbox]
            bw, bh = max(0.0, x2 - x1), max(0.0, y2 - y1)
            emb = np.asarray(f.normed_embedding, dtype=np.float32)
            lm = getattr(f, "landmark_2d_106", None)
            out.append(Face(
                path, (x1, y1, bw, bh), float(f.det_score), emb,
                (bw * bh) / float(w * h),
                kps=np.asarray(f.kps, dtype=np.float32) if f.kps is not None else None,
                landmarks=np.asarray(lm, dtype=np.float32) if lm is not None else None,
            ))
        return out


class _OpenCVBackend:
    name = "opencv"

    def __init__(self, det_size=640, on_msg=None, score_threshold=0.7):
        import cv2
        self.cv2 = cv2
        det_path = _download(*_YUNET, on_msg=on_msg)
        rec_path = _download(*_SFACE, on_msg=on_msg)
        self.det = cv2.FaceDetectorYN.create(
            str(det_path), "", (320, 320),
            score_threshold=score_threshold, nms_threshold=0.3, top_k=5000,
        )
        self.rec = cv2.FaceRecognizerSF.create(str(rec_path), "")

    def analyse(self, bgr, path):
        h, w = bgr.shape[:2]
        self.det.setInputSize((w, h))
        _, rows = self.det.detect(bgr)
        if rows is None:
            return []
        out = []
        for row in rows:
            x, y, bw, bh = [float(v) for v in row[:4]]
            score = float(row[-1])
            try:
                aligned = self.rec.alignCrop(bgr, row)
                feat = np.asarray(self.rec.feature(aligned), dtype=np.float32).ravel()
            except Exception:
                continue
            n = np.linalg.norm(feat)
            if not n:
                continue
            # YuNet gives 5 keypoints but no dense contour, so eye-openness
            # cannot be measured on this backend.
            kps = np.asarray(row[4:14], dtype=np.float32).reshape(5, 2)
            out.append(Face(path, (x, y, bw, bh), score, feat / n,
                            (bw * bh) / float(w * h), kps=kps))
        return out


_QUICK = {"det": None, "tried": False}


def quick_faces(bgr):
    """Face boxes (x, y, w, h) in one image, fast, for the zoom panel.

    Detection only - no recognition, no landmarks - with whichever detector is
    already on disk: the detection half of insightface's buffalo_l (what Sift
    uses here), else the small YuNet model. Glancing at a photo must never
    start a download. Returns [] when neither is there or anything goes wrong.
    """
    import threading

    lock = _QUICK.setdefault("lock", threading.Lock())
    with lock:
        if not _QUICK["tried"]:
            _QUICK["tried"] = True
            _QUICK["det"] = _quick_detector()
        det = _QUICK["det"]
        if det is None:
            return []
        try:
            return det(bgr)
        except Exception:
            return []


def _quick_detector():
    scrfd = Path.home() / ".insightface" / "models" / "buffalo_l" / "det_10g.onnx"
    if scrfd.is_file():
        try:
            import contextlib
            import io as _io

            with contextlib.redirect_stdout(_io.StringIO()):
                from insightface.model_zoo import get_model

                m = get_model(str(scrfd), providers=["CPUExecutionProvider"])
                m.prepare(ctx_id=-1, input_size=(640, 640), det_thresh=0.5)

            def run(bgr):
                boxes, _ = m.detect(bgr, max_num=0)
                return [(float(b[0]), float(b[1]), float(b[2] - b[0]), float(b[3] - b[1]))
                        for b in (boxes if boxes is not None else [])]
            return run
        except Exception:
            pass
    path = MODEL_DIR / _YUNET[0]
    if path.is_file() and path.stat().st_size > 100_000:
        try:
            import cv2

            yn = cv2.FaceDetectorYN.create(str(path), "", (320, 320),
                                           score_threshold=0.7, nms_threshold=0.3, top_k=50)

            def run(bgr):
                h, w = bgr.shape[:2]
                yn.setInputSize((w, h))
                _, rows = yn.detect(bgr)
                return [] if rows is None else [tuple(float(v) for v in r[:4]) for r in rows]
            return run
        except Exception:
            pass
    return None


def get_backend(prefer=None, det_size=640, on_msg=None):
    order = [prefer] if prefer else ["insightface", "opencv"]
    errors = []
    for name in order:
        try:
            if name == "insightface":
                return _InsightBackend(det_size, on_msg)
            if name == "opencv":
                return _OpenCVBackend(det_size, on_msg)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    if prefer:
        raise RuntimeError("; ".join(errors))
    try:
        return _OpenCVBackend(det_size, on_msg)
    except Exception as exc:
        errors.append(f"opencv: {exc}")
        raise RuntimeError("no face backend available -> " + " | ".join(errors))


def analyse_previews(preview_paths, backend, min_face_frac=0.0,
                     min_score=0.0, on_step=None):
    import cv2

    from .stop import checkpoint

    faces = []
    for p in preview_paths:
        checkpoint()                      # a face pass can take a second or two
        p = Path(p)
        img = cv2.imread(str(p))
        if img is None:
            if on_step:
                on_step(p, 0)
            continue
        found = [
            f for f in backend.analyse(img, p)
            if f.area_frac >= min_face_frac and f.score >= min_score
        ]
        faces.extend(found)
        if on_step:
            on_step(p, len(found))
    return faces


def cluster(faces, threshold=None, backend_name="opencv"):
    """Group faces into people. Returns a list of PersonGroup."""
    if not faces:
        return []
    if threshold is None:
        threshold = THRESHOLDS.get(backend_name, 0.6)

    embs = np.vstack([f.embedding for f in faces]).astype(np.float32)
    if len(faces) == 1:
        labels = np.array([0])
    else:
        from sklearn.cluster import AgglomerativeClustering

        model = AgglomerativeClustering(
            n_clusters=None, metric="cosine", linkage="average",
            distance_threshold=threshold,
        )
        labels = model.fit_predict(embs)

    groups = {}
    for face, lab in zip(faces, labels):
        lab = int(lab)
        face.cluster = lab
        groups.setdefault(lab, PersonGroup(lab, f"person_{lab + 1:02d}"))
        groups[lab].faces.append(face)

    ordered = sorted(groups.values(), key=lambda g: -len(g.images))
    for rank, g in enumerate(ordered, 1):
        if g.name.startswith("person_"):
            g.name = f"person_{rank:02d}"
    return ordered


def load_library():
    if LIBRARY.exists():
        try:
            return json.loads(LIBRARY.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_library(lib):
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY.write_text(json.dumps(lib, indent=2), encoding="utf-8")


def name_from_library(groups, backend_name, match_threshold=None):
    """Auto-name clusters that match someone named in a previous shoot."""
    lib = load_library()
    if not lib:
        return groups
    if match_threshold is None:
        match_threshold = THRESHOLDS.get(backend_name, 0.6) * 0.85

    known = []
    for name, rec in lib.items():
        if rec.get("backend") != backend_name:
            continue
        for c in rec.get("centroids", []):
            known.append((name, np.asarray(c, dtype=np.float32)))
    if not known:
        return groups

    taken = set()
    for g in groups:
        c = g.centroid()
        best, best_d = None, 1e9
        for name, vec in known:
            d = 1.0 - float(np.dot(c, vec))
            if d < best_d:
                best, best_d = name, d
        if best and best_d <= match_threshold and best not in taken:
            g.name = best
            taken.add(best)
    return groups


def remember(name, group, backend_name):
    """Teach the library a person, so the name sticks in later shoots."""
    lib = load_library()
    rec = lib.get(name)
    if not rec or rec.get("backend") != backend_name:
        rec = {"backend": backend_name, "centroids": [], "count": 0}
    rec["centroids"].append([float(x) for x in group.centroid()])
    rec["centroids"] = rec["centroids"][-8:]  # keep it from drifting
    rec["count"] = rec.get("count", 0) + len(group.images)
    lib[name] = rec
    save_library(lib)
    return rec


def save_face_crops(groups, out_dir, pad=0.35, size=256):
    """One reference crop per person, so a human can put a name to a cluster."""
    import cv2

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for g in groups:
        f = g.best_face()
        img = cv2.imread(str(f.image))
        if img is None:
            continue
        h, w = img.shape[:2]
        x, y, bw, bh = f.bbox
        px, py = bw * pad, bh * pad
        x0, y0 = max(0, int(x - px)), max(0, int(y - py))
        x1, y1 = min(w, int(x + bw + px)), min(h, int(y + bh + py))
        crop = img[y0:y1, x0:x1]
        if crop.size == 0:
            continue
        crop = cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)
        dest = out_dir / f"{g.name}.jpg"
        cv2.imwrite(str(dest), crop)
        written[g.name] = dest
    return written
