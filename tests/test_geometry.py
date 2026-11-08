"""Calibration and measurement geometry on fixtures with exactly known coordinates.

Each fixture is the kit's sheet rendered on a wall plane at 2 px/mm, warped by a known
perspective, with the true head-top, wrist and floor points carried through the same
warp. Body height 1772 mm and wrist-to-floor 849 mm in the wall plane.
"""
import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import photo
from app.server import app

PX_PER_MM = 2.0
FLOOR_Y, HEIGHT, WTF = 1300.0, 1772.0, 849.0          # sheet mm, y down; sheet top is 1.30 m above the floor
X0, Y0, X1, Y1 = -300.0, -560.0, 900.0, 1400.0
client = TestClient(app)


def wall(shift=(0.0, 0.0)):
    w, h = int((X1 - X0) * PX_PER_MM), int((Y1 - Y0) * PX_PER_MM)
    img = np.full((h, w, 3), 205, np.uint8)
    to_px = lambda x, y: ((x - X0) * PX_PER_MM, (y - Y0) * PX_PER_MM)
    sx, sy = map(int, to_px(0, 0))
    img[sy:sy + int(photo.A4_H * PX_PER_MM), sx:sx + int(photo.A4_W * PX_PER_MM)] = 250
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    size = int(40 * PX_PER_MM)
    for mid, (mx, my) in photo.MARKERS.items():
        x, y = map(int, to_px(mx, my))
        img[y:y + size, x:x + size] = cv2.cvtColor(cv2.aruco.generateImageMarker(d, mid, size), cv2.COLOR_GRAY2BGR)
    pts = {"head_top": to_px(560, FLOOR_Y - HEIGHT), "wrist": to_px(760, FLOOR_Y - WTF), "floor": to_px(600, FLOOR_Y)}
    paper = [to_px(0, 0), to_px(photo.A4_W, 0), to_px(photo.A4_W, photo.A4_H), to_px(0, photo.A4_H)]
    return img, pts, paper


def warp(img, pts, paper, k):
    """Perspective of strength k (0 = square-on): the far edge shrinks by k on each side."""
    h, w = img.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[w * k, h * k / 2], [w * (1 - k / 3), 0], [w, h], [0, h * (1 - k / 4)]])
    H = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(img, H, (w, h), borderValue=(205, 205, 205))
    tf = lambda p: cv2.perspectiveTransform(np.float32([[p]]), H)[0, 0].tolist()
    return out, {k_: tf(v) for k_, v in pts.items()}, [tf(c) for c in paper]


# observed on these fixtures: at most 0.6 cm error up to k = 0.08 (marker-corner detection, extrapolated to head and floor)
@pytest.mark.parametrize("k,tol_cm", [(0.0, 0.8), (0.02, 0.8), (0.05, 0.8), (0.08, 0.8)])
def test_markers_calibration_recovers_known_lengths(k, tol_cm):
    img, pts, paper = warp(*wall(), k)
    m = photo.measure(img, pts["head_top"], pts["wrist"], pts["floor"])
    assert m["calibration"] == "aruco_markers" and m["markers_found"] == [0, 1, 2, 3]
    assert m["height_cm"] == pytest.approx(HEIGHT / 10, abs=tol_cm)
    assert m["wrist_to_floor_cm"] == pytest.approx(WTF / 10, abs=tol_cm)


@pytest.mark.parametrize("k", [0.0, 0.02, 0.05])
def test_paper_corner_fallback_recovers_known_lengths(k):
    img, pts, paper = warp(*wall(), k)
    m = photo.measure(img, pts["head_top"], pts["wrist"], pts["floor"], corners=paper)
    assert m["calibration"] == "paper_corners"
    assert m["height_cm"] == pytest.approx(HEIGHT / 10, abs=0.3)
    assert m["wrist_to_floor_cm"] == pytest.approx(WTF / 10, abs=0.3)
    assert any("by hand" in w for w in m["warnings"])


def test_corner_clicks_off_by_a_few_pixels_stay_close():
    img, pts, paper = warp(*wall(), 0.02)
    rng = np.random.default_rng(1)
    jittered = [[x + rng.uniform(-3, 3), y + rng.uniform(-3, 3)] for x, y in paper]
    m = photo.measure(img, pts["head_top"], pts["wrist"], pts["floor"], corners=jittered)
    assert m["height_cm"] == pytest.approx(HEIGHT / 10, abs=4.0)     # 3 px on a ~420 px sheet, extrapolated 4x


def test_resized_photo_gives_the_same_result():
    img, pts, _ = warp(*wall(), 0.02)
    full = photo.measure(img, pts["head_top"], pts["wrist"], pts["floor"])
    small = cv2.resize(img, None, fx=0.6, fy=0.6, interpolation=cv2.INTER_AREA)
    sp = {k: [v[0] * 0.6, v[1] * 0.6] for k, v in pts.items()}
    half = photo.measure(small, sp["head_top"], sp["wrist"], sp["floor"])
    assert half["height_cm"] == pytest.approx(full["height_cm"], abs=0.6)
    assert half["wrist_to_floor_cm"] == pytest.approx(full["wrist_to_floor_cm"], abs=0.6)


def test_mirrored_photo_is_not_measured_with_markers():
    # ArUco codes are not mirror-symmetric: a mirrored (selfie-preview) image must never be the one measured
    img, pts, _ = warp(*wall(), 0.0)
    assert photo.find_sheet(cv2.flip(img, 1))["ok"] is False


@pytest.mark.parametrize("corners,needle", [
    ([[0, 0], [10, 0], [10, 10]], "all four"),
    ([[100, 100], [300, 100], [100, 400], [300, 400]], "cross"),            # TR and BR swapped: crossed
    ([[100, 100], [100, 400], [300, 400], [300, 100]], "cross"),            # anticlockwise order
    ([[100, 100], [101, 101], [300, 400], [100, 400]], "on top of each other"),
    ([[100, 100], [700, 100], [700, 250], [100, 250]], "isn't upright A4"),  # landscape / wrong shape
    ([[100, 100], [300, 100], [300, 400], [100, 9999]], "outside"),
])
def test_bad_corners_are_rejected(corners, needle):
    with pytest.raises(photo.PhotoError, match=needle):
        photo.validate_corners(corners, (2000, 3000))


def test_blur_warns_and_heavy_blur_fails():
    img, pts, _ = warp(*wall(), 0.0)
    m = photo.measure(cv2.GaussianBlur(img, (0, 0), 3), pts["head_top"], pts["wrist"], pts["floor"])
    assert any("blurry" in w for w in m["warnings"])
    with pytest.raises(photo.PhotoError, match="corner squares"):
        photo.measure(cv2.GaussianBlur(img, (0, 0), 8), pts["head_top"], pts["wrist"], pts["floor"])


def test_failure_paths():
    img, pts, _ = warp(*wall(), 0.0)
    with pytest.raises(photo.PhotoError, match="wrong order"):
        photo.measure(img, pts["floor"], pts["wrist"], pts["head_top"])
    with pytest.raises(photo.PhotoError, match="outside the photo"):
        photo.measure(img, [-50, 10], pts["wrist"], pts["floor"])
    blank = np.full_like(img, 200)
    with pytest.raises(photo.PhotoError, match="mark the paper's corners"):
        photo.measure(blank, pts["head_top"], pts["wrist"], pts["floor"])


def test_measure_api_accepts_corners():
    img, pts, paper = warp(*wall(), 0.02)
    ok, buf = cv2.imencode(".png", img)
    r = client.post("/api/photo/measure", files={"image": ("w.png", buf.tobytes(), "image/png")},
                    data={"points": json.dumps(pts), "corners": json.dumps(paper)})
    assert r.status_code == 200, r.text
    assert r.json()["calibration"] == "paper_corners"
    r = client.post("/api/photo/measure", files={"image": ("w.png", buf.tobytes(), "image/png")},
                    data={"points": json.dumps(pts), "corners": json.dumps(paper[:3])})
    assert r.status_code == 422
    assert client.get("/api/photo/thresholds").json()["prototype"] is True
