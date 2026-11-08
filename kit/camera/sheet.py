"""A4 measurement sheet with four ArUco markers: printing and metric geometry.

The sheet gives a phone photo a known scale and perspective. Anything lying in
the sheet's plane (a hand flat on the sheet, or a body standing against the
wall the sheet is taped to) can be measured in millimetres.

Coordinates: sheet millimetres, origin top-left, x right, y down. A4 = 210 x 297.
"""
from __future__ import annotations

import numpy as np

A4_W, A4_H = 210.0, 297.0
MARKER_MM = 40.0
MARGIN_MM = 12.0
DICT = "DICT_4X4_50"
# marker id -> top-left corner (mm) on the sheet
MARKERS = {0: (MARGIN_MM, MARGIN_MM), 1: (A4_W - MARGIN_MM - MARKER_MM, MARGIN_MM),
           2: (MARGIN_MM, A4_H - MARGIN_MM - MARKER_MM), 3: (A4_W - MARGIN_MM - MARKER_MM, A4_H - MARGIN_MM - MARKER_MM)}


def marker_corners_mm(mid: int) -> np.ndarray:
    x, y = MARKERS[mid]
    s = MARKER_MM
    return np.array([[x, y], [x + s, y], [x + s, y + s], [x, y + s]], float)   # TL TR BR BL (ArUco order)


def _cv2():
    import cv2
    return cv2


def detect(image_bgr):
    """Return (ids, corners_px) for sheet markers found in the image."""
    cv2 = _cv2()
    d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, DICT))
    det = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters())
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY) if image_bgr.ndim == 3 else image_bgr
    corners, ids, _ = det.detectMarkers(gray)
    if ids is None:
        return [], []
    keep = [(int(i), c.reshape(4, 2)) for i, c in zip(ids.ravel(), corners) if int(i) in MARKERS]
    return [k[0] for k in keep], [k[1] for k in keep]


class SheetPlane:
    """Homography between image pixels and sheet millimetres."""

    def __init__(self, image_bgr):
        cv2 = _cv2()
        ids, corners = detect(image_bgr)
        if len(ids) < 2:
            raise ValueError(f"found {len(ids)} sheet markers; need at least 2 (4 recommended)")
        src = np.concatenate(corners).astype(np.float32)
        dst = np.concatenate([marker_corners_mm(i) for i in ids]).astype(np.float32)
        self.H, _ = cv2.findHomography(src, dst, 0)
        self.ids = ids
        back = self.to_mm(src)
        self.reprojection_mm = float(np.sqrt(np.mean(np.sum((back - dst) ** 2, 1))))

    def to_mm(self, pts_px):
        p = np.asarray(pts_px, float).reshape(-1, 2)
        h = np.c_[p, np.ones(len(p))] @ self.H.T
        return h[:, :2] / h[:, 2:3]

    def distance_mm(self, p1_px, p2_px) -> float:
        a, b = self.to_mm([p1_px, p2_px])
        return float(np.linalg.norm(a - b))


def render_sheet_png(path, dpi=300):
    """Render the printable sheet as a PNG (the PDF maker wraps this)."""
    cv2 = _cv2()
    px = lambda mm: int(round(mm / 25.4 * dpi))
    img = np.full((px(A4_H), px(A4_W)), 255, np.uint8)
    d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, DICT))
    for mid, (x, y) in MARKERS.items():
        m = cv2.aruco.generateImageMarker(d, mid, px(MARKER_MM))
        img[px(y):px(y) + m.shape[0], px(x):px(x) + m.shape[1]] = m
    cv2.imwrite(str(path), img)
    return img
