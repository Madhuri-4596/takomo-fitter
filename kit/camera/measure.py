"""Measure hand length, height and wrist-to-floor from phone photos.

  python camera/measure.py hand  --image hand.jpg  [--points x1,y1,x2,y2] [--finger bx,by,tx,ty]
  python camera/measure.py body  --image body.jpg  [--landmarks body.json]

hand: lay the hand flat on the printed sheet, fingers together, wrist crease on
the sheet. Hand length = wrist crease to tip of the middle finger. Optional
finger length = base crease of the middle finger to its tip (PING's grip chart
uses both numbers; Takomo's tool uses glove size).
body: tape the sheet flat on a wall at about chest height; stand with your back
against the same wall in golf shoes, arms hanging. The photo is taken straight
on from 3-4 m. Everything is measured in the wall plane, so stand flat against it.

Points are image pixel coordinates: clicked by hand, or taken from any landmark
model (for example MediaPipe Hand Landmarker: wrist = 0, middle fingertip = 12;
MediaPipe Pose: wrists 15/16, heels 29/30). Model landmarks are approximations:
the wrist landmark sits near, not on, the wrist crease. Validate against a tape
measure and correct any systematic offset before trusting the numbers.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sheet import SheetPlane  # noqa: E402

GLOVE_EDGES_CM = [16.8, 17.8, 18.59, 19.4, 20.22]   # same as takomo_fit.products.GLOVE_EDGES_CM
GLOVES = ["XS", "S", "M", "M/L", "L", "XL"]


def hand_length_mm(plane: SheetPlane, wrist_px, middle_tip_px) -> float:
    return plane.distance_mm(wrist_px, middle_tip_px)


def body_measures_cm(plane: SheetPlane, head_top_px, wrist_px, floor_px):
    """Height and wrist-to-floor from three points in the wall plane.

    floor_px: a point on the floor line under the golfer (e.g. heel contact).
    Vertical distances are taken along the sheet's own vertical axis, so a
    slightly rotated phone does not bias them.
    """
    top, wr, fl = plane.to_mm([head_top_px, wrist_px, floor_px])
    height = abs(fl[1] - top[1]) / 10
    wtf = abs(fl[1] - wr[1]) / 10
    return round(height, 1), round(wtf, 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["hand", "body"])
    ap.add_argument("--image", required=True)
    ap.add_argument("--points", help="hand: wrist_x,wrist_y,tip_x,tip_y in pixels")
    ap.add_argument("--finger", help="hand, optional: finger_base_x,finger_base_y,tip_x,tip_y in pixels")
    ap.add_argument("--landmarks", help="body: JSON {head_top:[x,y], wrist:[x,y], floor:[x,y]} in pixels")
    a = ap.parse_args(argv)
    import cv2
    img = cv2.imread(a.image)
    if img is None:
        sys.exit(f"cannot read {a.image}")
    plane = SheetPlane(img)
    out = {"markers_found": plane.ids, "reprojection_mm": round(plane.reprojection_mm, 2)}
    if a.mode == "hand":
        if not a.points:
            sys.exit("give --points wrist_x,wrist_y,tip_x,tip_y")
        x1, y1, x2, y2 = map(float, a.points.split(","))
        mm = hand_length_mm(plane, (x1, y1), (x2, y2))
        cm = mm / 10
        out.update(hand_length_cm=round(cm, 2), glove_estimate=GLOVES[int(np.digitize(cm, GLOVE_EDGES_CM))])
        if a.finger:
            bx, by, tx, ty = map(float, a.finger.split(","))
            out["finger_length_cm"] = round(plane.distance_mm((bx, by), (tx, ty)) / 10, 2)
    else:
        if not a.landmarks:
            sys.exit("give --landmarks body.json")
        L = json.loads(Path(a.landmarks).read_text())
        h, w = body_measures_cm(plane, L["head_top"], L["wrist"], L["floor"])
        out.update(height_cm=h, wrist_to_floor_cm=w,
                   note="add nothing for shoes if photographed in golf shoes, as the questionnaire asks")
    if plane.reprojection_mm > 1.5:
        out["warning"] = "sheet fit is poor (reprojection > 1.5 mm): retake the photo flatter and sharper"
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    main()
