"""Build the sample full-body photo used by the app's "Sample photo" button and the tests.

A real person (a public-domain Library of Congress studio portrait, so pose tracking
can find him) composited next to the kit's A4 measurement sheet on a wall, scaled
so he is 177.2 cm tall, then seen with a mild perspective tilt. The true height and
wrist-to-floor and the pixel positions of the three measuring points are written
next to the image.

Source: "Unidentified man, facing front, standing next to chair, posing for
full-length studio portrait", Library of Congress LCCN 2015649998, public domain.

  .venv/Scripts/python tools/make_sample_photo.py      (downloads the portrait once)
"""
from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kit" / "camera"))
from sheet import A4_H, A4_W, DICT, MARKER_MM, MARKERS  # noqa: E402

SOURCE_URL = ("https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Unidentified_man%2C_facing_front%2C_standing_next_to_chair"
              "%2C_posing_for_full-length_studio_portrait_LCCN2015649998.jpg/960px-Unidentified_man%2C_facing_front%2C_standing_"
              "next_to_chair%2C_posing_for_full-length_studio_portrait_LCCN2015649998.jpg")
# measuring points in the 960 x 1482 source, located by eye on 2x crops (+-5 px)
SRC_HEAD_TOP, SRC_WRIST, SRC_FLOOR = (485, 221), (570, 828), (500, 1325)
SRC_CROP = (90, 80, 880, 1400)               # inside the card border
HEIGHT_MM = 1772.0
FLOOR_Y = 1300.0                              # sheet top edge is 1.30 m above the floor (sheet mm, y down)
PERSON_LEFT = 300.0                           # portrait's left edge, mm right of the sheet's left edge


def source() -> np.ndarray:
    cache = Path(tempfile.gettempdir()) / "takomo_sample_person_source.jpg"
    if not cache.exists():
        req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "TakomoFitter/1.0 (hackathon sample photo)"})
        cache.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    img = cv2.imread(str(cache))
    if img is None or img.shape[:2] != (1482, 960):
        sys.exit(f"unexpected source image at {cache}; delete it and run again")
    return img


def compose() -> tuple[np.ndarray, dict, float]:
    src = source()
    s = HEIGHT_MM / (SRC_FLOOR[1] - SRC_HEAD_TOP[1])            # mm per source pixel
    x0, y0, x1, y1 = SRC_CROP
    person = cv2.resize(src[y0:y1, x0:x1], None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    px, py = PERSON_LEFT, FLOOR_Y - (SRC_FLOOR[1] - y0) * s       # portrait's top-left in sheet mm
    X0, Y0 = -260.0, py - 20
    X1, Y1 = px + person.shape[1] + 20, FLOOR_Y + 140
    img = np.full((int(Y1 - Y0), int(X1 - X0), 3), (196, 214, 226), np.uint8)       # 1 px = 1 mm: wall
    cv2.rectangle(img, (0, int(FLOOR_Y - Y0)), (img.shape[1], img.shape[0]), (92, 110, 128), -1)
    to_px = lambda x, y: (int(round(x - X0)), int(round(y - Y0)))
    ox, oy = to_px(px, py)
    img[oy:oy + person.shape[0], ox:ox + person.shape[1]] = person
    # the sheet, at exact millimetre scale so its geometry is exact
    sx, sy = to_px(0, 0)
    img[sy:sy + int(A4_H), sx:sx + int(A4_W)] = 250
    d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, DICT))
    for mid, (mx, my) in MARKERS.items():
        m = cv2.aruco.generateImageMarker(d, mid, int(MARKER_MM))
        x, y = to_px(mx, my)
        img[y:y + int(MARKER_MM), x:x + int(MARKER_MM)] = cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)
    cv2.line(img, to_px(55, 148), to_px(155, 148), (0, 0, 0), 2)              # 100 mm check bar
    pt = lambda p: to_px(px + (p[0] - x0) * s, py + (p[1] - y0) * s)
    truth = {"head_top": pt(SRC_HEAD_TOP), "wrist": pt(SRC_WRIST), "floor": pt(SRC_FLOOR)}
    truth["paper"] = [to_px(0, 0), to_px(A4_W, 0), to_px(A4_W, A4_H), to_px(0, A4_H)]   # TL, TR, BR, BL
    wtf_mm = (SRC_FLOOR[1] - SRC_WRIST[1]) * s
    return img, truth, wtf_mm


def main():
    flat, truth, wtf_mm = compose()
    h, w = flat.shape[:2]
    # mild perspective: phone held by a friend, slightly off square
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[w * 0.008, h * 0.004], [w * 0.995, 0], [w * 1.0, h], [0, h * 0.997]])
    H = cv2.getPerspectiveTransform(src, dst)
    photo = cv2.warpPerspective(flat, H, (w, h), borderMode=cv2.BORDER_REPLICATE)
    rng = np.random.default_rng(13)
    photo = np.clip(photo.astype(np.int16) + rng.normal(0, 3, photo.shape), 0, 255).astype(np.uint8)
    tf = lambda v: cv2.perspectiveTransform(np.float32([[v]]), H)[0, 0].round(1).tolist()
    pts = {k: ([tf(c) for c in v] if k == "paper" else tf(v)) for k, v in truth.items()}
    out = ROOT / "app" / "static" / "sample"
    out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "sample-body.jpg"), photo, [cv2.IMWRITE_JPEG_QUALITY, 88])
    (out / "sample-body.json").write_text(json.dumps(
        {"height_cm": HEIGHT_MM / 10, "wrist_to_floor_cm": round(wtf_mm / 10, 1), "image_size": [w, h], "points": pts,
         "note": "Composite of a public-domain Library of Congress portrait (LCCN 2015649998) and the measurement sheet. "
                 "Points are the true measuring points in image pixels, located by eye on the source (+-5 px)."}, indent=2))
    print(f"wrote {out / 'sample-body.jpg'} ({w}x{h}), wrist-to-floor {wtf_mm / 10:.1f} cm", pts)


if __name__ == "__main__":
    main()
