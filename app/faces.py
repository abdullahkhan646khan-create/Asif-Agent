"""Faces in the AI picture, so no headline, card or logo is ever drawn over a face.

Uses OpenCV's small YuNet face detector (app/assets/models/face_detection_yunet_2023mar.onnx, MIT licence,
github.com/opencv/opencv_zoo). If OpenCV can't be loaded, no faces are reported and posts are made as before.
"""

import logging
from pathlib import Path

import numpy as np
from PIL import Image

log = logging.getLogger("faces")
MODEL = Path(__file__).resolve().parent / "assets" / "models" / "face_detection_yunet_2023mar.onnx"


def find_faces(img: Image.Image, min_score: float = 0.6) -> list[list[int]]:
    """Boxes [x0, y0, x1, y1] in the picture's pixels, padded to the whole head (hair or helmet, chin)."""
    try:
        import cv2  # loaded only when needed: it is big
    except Exception as e:  # pragma: no cover - only when OpenCV is missing
        log.warning("Face check off: OpenCV not available (%s)", e)
        return []
    try:
        w, h = img.size
        k = min(1.0, 1280 / max(w, h))  # a smaller copy is faster and finds the same faces
        small = img.convert("RGB").resize((max(1, int(w * k)), max(1, int(h * k))))
        bgr = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2BGR)
        detector = cv2.FaceDetectorYN.create(str(MODEL), "", (bgr.shape[1], bgr.shape[0]), min_score, 0.3, 50)
        _, found = detector.detect(bgr)
    except Exception as e:
        log.warning("Face check failed: %s", e)
        return []
    boxes = []
    for f in found if found is not None else []:
        x, y, fw, fh = (float(v) / k for v in f[:4])
        boxes.append([int(x - fw * 0.25), int(y - fh * 0.45), int(x + fw * 1.25), int(y + fh * 1.25)])
    return boxes


def _free_mask(w: int, h: int, zones: list, step: int) -> np.ndarray:
    mask = np.zeros((h // step + 1, w // step + 1), bool)
    for x0, y0, x1, y1 in zones:
        mask[max(0, y0) // step:max(0, min(h, y1)) // step + 1, max(0, x0) // step:max(0, min(w, x1)) // step + 1] = True
    return mask


def best_focus(faces: list, img_w: int, img_h: int, w: int, h: int, zones: list,
               default: tuple[float, float], step: int = 8) -> tuple[tuple[float, float], float]:
    """The crop (focus) that keeps the faces in the free zones, closest to the layout's usual crop.
    Zones are boxes inside the w×h crop where a face may be (no text, card or logo there).
    Returns (focus, share of the faces still covered: 0 = all free)."""
    if not faces:
        return default, 0.0
    scale = max(w / img_w, h / img_h)
    spare_x, spare_y = img_w * scale - w, img_h * scale - h
    xs = np.linspace(0, 1, 21) if spare_x > 2 else np.array([default[0]])
    ys = np.linspace(0, 1, 21) if spare_y > 2 else np.array([default[1]])
    mask = _free_mask(w, h, zones, step)
    # each face as a grid of sample points (in the scaled picture), moved for every candidate crop
    samples = []
    for x0, y0, x1, y1 in faces:
        gx = np.arange(x0 * scale, x1 * scale + 1, step)
        gy = np.arange(y0 * scale, y1 * scale + 1, step)
        if len(gx) and len(gy):
            samples.append((gx, gy))
    best = None
    for fx in xs:
        for fy in ys:
            left, top = spare_x * fx, spare_y * fy
            bad = 0.0
            for gx, gy in samples:
                cx, cy = gx - left, gy - top
                inside = (cx[None, :] >= 0) & (cx[None, :] < w) & (cy[:, None] >= 0) & (cy[:, None] < h)
                xi = np.clip((cx // step).astype(int), 0, mask.shape[1] - 1)
                yi = np.clip((cy // step).astype(int), 0, mask.shape[0] - 1)
                bad += 1 - (inside & mask[yi[:, None], xi[None, :]]).mean()
            bad /= max(len(samples), 1)
            cost = bad * 10 + (abs(fx - default[0]) + abs(fy - default[1])) * 0.3
            if best is None or cost < best[0]:
                best = (cost, (float(fx), float(fy)), float(bad))
    return best[1], best[2]
