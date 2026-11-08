"""Body measurement from a phone photo, using the kit's camera code.

Scale comes from the printed sheet: the kit's four ArUco markers (camera/sheet.py,
DICT_4X4_50, 40 mm, ids 0-3) give an image-to-millimetre homography in the wall
plane. If the markers can't be found, the golfer can mark the paper's four outside
corners instead (top-left, top-right, bottom-right, bottom-left), mapped to an
A4 rectangle of 210 x 297 mm. Height and wrist-to-floor then come from the kit's
measure.body_measures_cm, along the sheet's vertical axis.

The photo is decoded in memory, measured and discarded; nothing is written to disk.
"""
from __future__ import annotations

import sys

import cv2
import numpy as np

from .fitting import KIT

sys.path.insert(0, str(KIT / "camera"))
from measure import body_measures_cm  # noqa: E402
from sheet import A4_H, A4_W, MARKERS, SheetPlane, detect as kit_detect, marker_corners_mm  # noqa: E402

MAX_BYTES = 15 * 1024 * 1024

# Prototype thresholds. They are engineering guesses checked only on synthetic images,
# NOT validated measurement-error limits; replace them after tape-measure validation.
THRESHOLDS = {
    "reprojection_mm_max": 1.5,          # kit/camera/measure.py: sheet fit is poor above this
    "sharpness_min": 100.0,              # variance of the Laplacian over the sheet area (markers path)
    "sharpness_whole_min": 15.0,         # ... over the whole photo (paper-corner path: no markers to look at)
    "height_cm_range": (130.0, 215.0),
    "wtf_to_height_ratio_range": (0.42, 0.58),
    "corner_min_side_px": 30.0,
    "corner_angle_deg_range": (35.0, 145.0),
    "corner_aspect_range": (0.9, 2.3),   # paper height / width in the image (A4 is 1.41)
    "cross_check_height_cm": 4.0,        # photo vs entered height: explain a difference above this
    "cross_check_wtf_cm": 4.0,
    "shoe_allowance_cm": 2.5,            # added to barefoot height and wrist-to-floor to get the golf-shoe values
    "max_body_tilt_deg": 8.0,            # head-to-floor line this far from the image vertical: camera not level
}

# Assumed measurement error per method (cm, wrist-to-floor), used to decide when a tape check is needed.
# PROTOTYPE ASSUMPTIONS, not measured: printed sheet from the kit's simulator (2.0 cm); the others are guesses
# that need tape-measure validation on real people.
METHOD_ERROR_CM = {"printed_sheet": 2.0, "plain_paper": 2.5, "entered_height": 3.0, "tape": 1.0, "simulated": 2.0}


class PhotoError(ValueError):
    pass


def decode(data: bytes) -> np.ndarray:
    if not data:
        raise PhotoError("The photo is empty.")
    if len(data) > MAX_BYTES:
        raise PhotoError("The photo is larger than 15 MB. Try a smaller image.")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise PhotoError("We couldn't read that file as an image. Use a JPEG or PNG photo.")
    return img


def sharpness(img: np.ndarray, quad: np.ndarray | None = None) -> float:
    """Variance of the Laplacian, over the sheet's bounding box when known."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if quad is not None and len(quad):
        # the sheet's box grown by 20 %: includes its edges and surroundings, so blank paper doesn't read as blur
        lo, hi = np.asarray(quad, float).min(0), np.asarray(quad, float).max(0)
        pad = 0.2 * (hi - lo)
        x0, y0 = np.floor(lo - pad).astype(int)
        x1, y1 = np.ceil(hi + pad).astype(int)
        h, w = gray.shape
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
        if x1 - x0 > 8 and y1 - y0 > 8:
            gray = gray[y0:y1, x0:x1]
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def detect(img: np.ndarray):
    """The kit's marker finder, made to work from across a room. The kit's defaults first; if that finds fewer than
    four markers, again on a 2x upscaled image with a lower minimum marker size (small markers at 3 m). Every corner is
    then refined to sub-pixel precision, which is what the scale, and so the height, rests on."""
    ids, corners = kit_detect(img)
    if len(ids) < 4:
        d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        prm = cv2.aruco.DetectorParameters()
        prm.minMarkerPerimeterRate = 0.01
        prm.adaptiveThreshWinSizeMax = 53
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        big = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        c2, i2, _ = cv2.aruco.ArucoDetector(d, prm).detectMarkers(big)
        if i2 is not None:
            found = [(int(i), c.reshape(4, 2) / 2.0) for i, c in zip(i2.ravel(), c2) if int(i) in MARKERS]
            if len(found) > len(ids):
                ids, corners = [f[0] for f in found], [f[1] for f in found]
    if ids:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.01)
        refined = []
        for c in corners:
            side = float(np.linalg.norm(c[0] - c[1]))
            win = max(2, min(8, int(side / 8)))
            pts = cv2.cornerSubPix(gray, c.astype(np.float32).reshape(-1, 1, 2), (win, win), (-1, -1), crit)
            refined.append(pts.reshape(4, 2).astype(float))
        corners = refined
    return ids, corners


class MarkerPlane(SheetPlane):
    """The kit's SheetPlane, built from our (sharper) detections instead of the kit's."""

    def __init__(self, img: np.ndarray):
        ids, corners = detect(img)
        if len(ids) < 2:
            raise ValueError(f"found {len(ids)} sheet markers; need at least 2 (4 recommended)")
        src = np.concatenate(corners).astype(np.float32)
        dst = np.concatenate([marker_corners_mm(i) for i in ids]).astype(np.float32)
        self.H, _ = cv2.findHomography(src, dst, 0)
        self.ids = ids
        back = self.to_mm(src)
        self.reprojection_mm = float(np.sqrt(np.mean(np.sum((back - dst) ** 2, 1))))


def find_sheet(img: np.ndarray) -> dict:
    ids, corners = detect(img)
    quad = np.concatenate(corners) if corners else None
    sharp = round(sharpness(img, quad), 1)
    out = {"markers_found": sorted(ids), "markers_expected": len(MARKERS),
           "markers": [{"id": i, "corners": c.round(1).tolist()} for i, c in zip(ids, corners)],
           "image_size": [int(img.shape[1]), int(img.shape[0])], "sharpness": sharp}
    if len(ids) < 2:
        out["ok"] = False
        out["message"] = (f"We found {len(ids)} of the 4 corner squares on the sheet. Make sure the whole sheet is in "
                          "the photo, flat on the wall, well lit and in focus, or mark the paper's corners by hand.")
    else:
        out["ok"] = True
        out["message"] = ("Sheet found." if len(ids) == 4 else
                          f"Sheet found ({len(ids)} of 4 corner squares). It will work, but all four is more accurate.")
    return out


class CornerPlane:
    """Homography from four hand-marked paper corners (TL, TR, BR, BL) to A4 millimetres."""

    def __init__(self, corners_px, image_size):
        c = validate_corners(corners_px, image_size)
        dst = np.float32([[0, 0], [A4_W, 0], [A4_W, A4_H], [0, A4_H]])
        self.H = cv2.getPerspectiveTransform(c, dst)
        self.reprojection_mm = None        # four points define the fit exactly: no residual to report

    def to_mm(self, pts_px):
        p = np.asarray(pts_px, float).reshape(-1, 2)
        h = np.c_[p, np.ones(len(p))] @ self.H.T
        return h[:, :2] / h[:, 2:3]


def validate_corners(corners_px, image_size) -> np.ndarray:
    """Reject corners that are missing, outside the image, crossed, overlapping or degenerate."""
    try:
        c = np.asarray(corners_px, np.float32).reshape(4, 2)
    except (ValueError, TypeError):
        raise PhotoError("Mark all four paper corners: top-left, top-right, bottom-right, bottom-left.")
    w, h = image_size
    if np.any(c < -2) or np.any(c[:, 0] > w + 2) or np.any(c[:, 1] > h + 2):
        raise PhotoError("A corner is outside the photo. Mark the corners again.")
    sides = np.linalg.norm(np.roll(c, -1, 0) - c, axis=1)
    if sides.min() < THRESHOLDS["corner_min_side_px"]:
        raise PhotoError("Two corners are on top of each other. Mark each corner of the paper once.")
    # consistent turn direction at every corner = convex and not crossed; in image
    # coordinates (y down) TL -> TR -> BR -> BL turns clockwise, so every cross product is positive
    e = np.roll(c, -1, 0) - c
    cross = e[:, 0] * np.roll(e, -1, 0)[:, 1] - e[:, 1] * np.roll(e, -1, 0)[:, 0]
    if not np.all(cross > 0):
        raise PhotoError("The corners cross or are in the wrong order. Tap top-left, top-right, bottom-right, "
                         "then bottom-left.")
    lo, hi = THRESHOLDS["corner_angle_deg_range"]
    for i in range(4):
        a, b = c[i - 1] - c[i], c[(i + 1) % 4] - c[i]
        ang = np.degrees(np.arccos(np.clip(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)), -1, 1)))
        if not lo <= ang <= hi:
            raise PhotoError("Those corners don't look like a sheet of paper. Check each corner and try again.")
    aspect = (sides[1] + sides[3]) / (sides[0] + sides[2])
    lo, hi = THRESHOLDS["corner_aspect_range"]
    if not lo <= aspect <= hi:
        raise PhotoError("The marked shape isn't upright A4 paper. Tape the sheet upright and mark its corners in order.")
    return c


def measure(img: np.ndarray, head_top, wrist, floor, corners=None) -> dict:
    size = (int(img.shape[1]), int(img.shape[0]))
    for name, p in (("head top", head_top), ("wrist", wrist), ("floor", floor)):
        if not (0 <= p[0] <= size[0] and 0 <= p[1] <= size[1]):
            raise PhotoError(f"The {name} marker is outside the photo.")
    if not (head_top[1] < wrist[1] < floor[1]):
        raise PhotoError("The markers are in the wrong order. The head top must be highest, then the wrist crease, "
                         "then the floor.")
    warnings = []
    if corners is not None:
        plane = CornerPlane(corners, size)
        sheet = {"ok": True, "calibration": "paper_corners", "markers_found": [], "markers": [],
                 "image_size": list(size), "sharpness": round(sharpness(img), 1),
                 "message": "Scale from the paper corners you marked."}
        sharp_min = THRESHOLDS["sharpness_whole_min"]
        warnings.append("Scale comes from corners you marked by hand, which is less precise than the printed markers.")
    else:
        sheet = find_sheet(img)
        if not sheet["ok"]:
            raise PhotoError(sheet["message"])
        plane = MarkerPlane(img)
        sheet["calibration"] = "aruco_markers"
        sharp_min = THRESHOLDS["sharpness_min"]
        if plane.reprojection_mm > THRESHOLDS["reprojection_mm_max"]:
            warnings.append("The sheet looks bent or blurry. Retake the photo with the sheet flat and the phone steady.")
    if sheet["sharpness"] < sharp_min:
        warnings.append("The photo looks blurry around the sheet. Retake it with the phone steady and good light.")
    height, wtf = body_measures_cm(plane, head_top, wrist, floor)
    lo, hi = THRESHOLDS["height_cm_range"]
    if not lo <= height <= hi:
        raise PhotoError(f"That gives a height of {height:.0f} cm, which looks wrong. Check the three markers and that "
                         "you were standing against the same wall as the sheet.")
    ratio = wtf / height
    lo, hi = THRESHOLDS["wtf_to_height_ratio_range"]
    if not lo <= ratio <= hi:
        warnings.append(f"Wrist-to-floor is {ratio:.0%} of your height; most people are 46–52 %. "
                        "Check the wrist marker sits on the crease where hand meets arm.")
    return {**sheet, "height_cm": float(height), "wrist_to_floor_cm": float(wtf),
            "reprojection_mm": None if plane.reprojection_mm is None else round(plane.reprojection_mm, 2),
            "warnings": warnings, "thresholds_note": "Checks use prototype thresholds, not validated error limits."}


def measure_with_height(head_top, wrist, floor, height_cm: float, footwear: str = "golf_shoes") -> dict:
    """Wrist-to-floor from the golfer's entered height: no paper, and no image leaves the device.

    The ratio (floor to wrist) / (floor to head top), both measured along the body's own axis in the
    image, times the entered height. Assumes a level camera at about waist height and the head, wrist
    and floor at a similar distance from the camera; the result's error must be validated on real people.
    """
    h, w, f = (np.asarray(p, float) for p in (head_top, wrist, floor))
    if not 120 <= height_cm <= 220:
        raise PhotoError("Enter your height in centimetres (120 to 220) first.")
    if not (h[1] < w[1] < f[1]):
        raise PhotoError("The markers are in the wrong order. The head top must be highest, then the wrist crease, "
                         "then the floor.")
    axis = h - f
    full = float(np.linalg.norm(axis))
    if full < 50:
        raise PhotoError("The head and floor markers are too close together. Place them on the photo again.")
    axis /= full
    tilt = float(np.degrees(np.arccos(abs(axis[1]))))
    ratio = float(np.dot(w - f, axis)) / full
    warnings = []
    if tilt > THRESHOLDS["max_body_tilt_deg"]:
        warnings.append(f"The body looks tilted by {tilt:.0f}° in the photo. Hold the phone level and retake it for a better estimate.")
    lo, hi = THRESHOLDS["wtf_to_height_ratio_range"]
    if not lo <= ratio <= hi:
        warnings.append(f"Wrist-to-floor comes out at {ratio:.0%} of your height; most people are 46–52 %. "
                        "Check the wrist marker sits on the crease and the floor marker under your heel.")
    allowance = THRESHOLDS["shoe_allowance_cm"] if footwear == "barefoot" else 0.0
    wtf = ratio * height_cm + allowance
    return {"calibration": "entered_height", "height_cm": round(height_cm + allowance, 1),
            "wrist_to_floor_cm": round(wtf, 1), "ratio": round(ratio, 4), "body_tilt_deg": round(tilt, 1),
            "footwear": footwear, "shoe_allowance_cm": allowance, "assumed_error_cm": METHOD_ERROR_CM["entered_height"],
            "warnings": warnings, "thresholds_note": "Checks use prototype thresholds, not validated error limits."}
